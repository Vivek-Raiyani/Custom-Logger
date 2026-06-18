"""
Log ingestion endpoint.

Flow
----
1. Authenticate request via project API key.
2. Persist every entry to ``log_entries`` table (source of truth).
3. Also write to the rotating NDJSON file (cheap tail / grep).
4. After persisting, run alert checks for each entry.

Alert check logic
-----------------
For every active AlertRule on the project:
  a. Does the incoming log entry match the rule's (service_label, match_field, match_value)?
  b. If yes, count how many log_entries with the same fingerprint exist in the last window_seconds.
  c. If count >= threshold AND no AlertEvent fired for this rule in the last window_seconds → fire.
"""

import hashlib
import json
import logging
import logging.handlers
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import CurrentUser, DB
from app.database.db import get_db
from app.database.models import AlertEvent, AlertRule, LogEntry, Project

router = APIRouter(prefix="/logs", tags=["logs"])

_file_logger: logging.Logger | None = None


# ============================================================================
# File logger (NDJSON rotating file — kept for tail/grep convenience)
# ============================================================================


def _get_file_logger() -> logging.Logger:
    global _file_logger
    if _file_logger is not None:
        return _file_logger

    settings = get_settings()
    log_path = Path(settings.log_file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("lycan.ingest")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    _file_logger = logger
    return logger


# ============================================================================
# Schemas
# ============================================================================

LogLevel = Literal["debug", "info", "warning", "error", "critical"]


class LogEntry_In(BaseModel):
    """Payload shape sent by the SDK."""

    level: LogLevel = "info"
    message: str
    timestamp: float | None = None

    # SDK caller sets these for precise alert matching
    service_label: str | None = None   # e.g. "payment-service"
    error_code: str | None = None      # e.g. "ERR_PAYMENT_API"

    module: str | None = None
    function: str | None = None
    request_id: str | None = None
    extra: dict | None = None


class LogBatch(BaseModel):
    entries: list[LogEntry_In]


class LogResponse(BaseModel):
    accepted: int


class LogEntryOut(BaseModel):
    id: str
    ts: float
    level: str
    message: str
    service_label: str | None
    error_code: str | None
    module: str | None
    function: str | None
    request_id: str | None
    extra: dict | None
    fingerprint: str | None
    created_at: str

    model_config = {"from_attributes": True}


# ============================================================================
# Fingerprint
# ============================================================================


def _compute_fingerprint(
    project_id: str,
    service_label: str | None,
    level: str,
    error_code: str | None,
    message: str,
) -> str:
    """
    Stable 16-char bucket key for deduplication.

    Priority:
      - If error_code is set → use it (most precise).
      - Otherwise → use first 64 chars of message (groups same error text).

    Two logs with different error_code will always get different fingerprints,
    which is exactly what we want: "ERR_PAYMENT_API" ≠ "ERR_DB_CONN" even
    if both come from "payment-service" at level "error".
    """
    discriminator = error_code if error_code else message[:64]
    raw = f"{project_id}:{service_label or ''}:{level}:{discriminator}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ============================================================================
# Alert logic
# ============================================================================


def _entry_matches_rule(entry: LogEntry_In, rule: AlertRule) -> bool:
    """Return True if this log entry satisfies the rule's match criteria."""

    # Service scope check
    if rule.service_label is not None:
        if entry.service_label != rule.service_label:
            return False

    # Field match
    if rule.match_field == "level":
        return entry.level == rule.match_value
    elif rule.match_field == "error_code":
        return entry.error_code == rule.match_value
    elif rule.match_field == "message":
        return rule.match_value.lower() in entry.message.lower()

    return False


async def _check_and_fire_alerts(
    db,
    project_id: str,
    entry: LogEntry_In,
    fingerprint: str,
) -> None:
    """
    For every active rule on this project that matches the entry:
      1. Count matching log_entries in the rule's window.
      2. If count >= threshold and no alert fired in this window → fire.
    """

    # Fetch active rules for this project
    rules_result = await db.execute(
        select(AlertRule).where(
            AlertRule.project_id == project_id,
            AlertRule.is_active == True,  # noqa: E712
        )
    )
    rules = rules_result.scalars().all()

    now = datetime.now(timezone.utc)

    for rule in rules:
        if not _entry_matches_rule(entry, rule):
            continue

        window_start_ts = (now - timedelta(seconds=rule.window_seconds)).timestamp()

        # Count matching entries in window using the fingerprint
        count_result = await db.execute(
            select(func.count()).where(
                LogEntry.project_id == project_id,
                LogEntry.fingerprint == fingerprint,
                LogEntry.ts >= window_start_ts,
            )
        )
        count = count_result.scalar_one()

        if count < rule.threshold:
            continue

        # Cooldown check — did we already fire this rule within the window?
        window_start_dt = now - timedelta(seconds=rule.window_seconds)
        recent_event_result = await db.execute(
            select(AlertEvent).where(
                AlertEvent.rule_id == rule.id,
                AlertEvent.fired_at >= window_start_dt,
            )
        )
        already_fired = recent_event_result.scalar_one_or_none()

        if already_fired is not None:
            continue  # still in cooldown

        # Fire the alert
        _send_alert_email(
            to=rule.notify_email,
            rule_name=rule.name,
            project_id=project_id,
            service_label=entry.service_label,
            match_field=rule.match_field,
            match_value=rule.match_value,
            count=count,
            window_seconds=rule.window_seconds,
            sample_message=entry.message,
        )

        # Record the event so we don't re-fire within this window
        event = AlertEvent(
            rule_id=rule.id,
            fingerprint=fingerprint,
            occurrence_count=count,
        )
        db.add(event)

    await db.commit()


# ============================================================================
# Dummy email sender  (replace with real SMTP / SendGrid / etc. later)
# ============================================================================


def _send_alert_email(
    *,
    to: str,
    rule_name: str,
    project_id: str,
    service_label: str | None,
    match_field: str,
    match_value: str,
    count: int,
    window_seconds: int,
    sample_message: str,
) -> None:
    """
    Placeholder — prints to stdout.
    Replace the body of this function with real email delivery later.
    """
    minutes = window_seconds // 60
    print("=" * 60)
    print(f"[ALERT] {rule_name}")
    print(f"  To:           {to}")
    print(f"  Project:      {project_id}")
    print(f"  Service:      {service_label or 'all'}")
    print(f"  Match:        {match_field} = '{match_value}'")
    print(f"  Occurrences:  {count} in last {minutes} min")
    print(f"  Sample:       {sample_message[:120]}")
    print("=" * 60)


# ============================================================================
# Dependencies
# ============================================================================


async def get_project_from_api_key(
    db: DB,
    authorization: str = Header(...),
) -> Project:
    """Authenticate SDK requests using a project API key."""

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
        )

    token = authorization[7:].strip()
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    result = await db.execute(
        select(Project).where(
            Project.api_key_hash == token_hash,
            Project.is_active.is_(True),
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return project


# ============================================================================
# Routes
# ============================================================================


@router.post("", response_model=LogResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_logs(
    body: LogBatch,
    db: DB,
    project: Project = Depends(get_project_from_api_key),
) -> LogResponse:
    """Accept a batch of log entries from the SDK."""

    if not body.entries:
        return LogResponse(accepted=0)

    file_logger = _get_file_logger()
    now_ts = datetime.now(timezone.utc).timestamp()

    orm_entries: list[LogEntry] = []

    for entry in body.entries:
        ts = entry.timestamp or now_ts
        fp = _compute_fingerprint(
            project_id=str(project.id),
            service_label=entry.service_label,
            level=entry.level,
            error_code=entry.error_code,
            message=entry.message,
        )

        # 1. Build ORM row
        orm_entry = LogEntry(
            project_id=str(project.id),
            ts=ts,
            level=entry.level,
            message=entry.message,
            service_label=entry.service_label,
            error_code=entry.error_code,
            module=entry.module,
            function=entry.function,
            request_id=entry.request_id,
            extra=entry.extra,
            fingerprint=fp,
        )
        orm_entries.append(orm_entry)
        db.add(orm_entry)

        # 2. Write to NDJSON file
        record = {
            "ts": ts,
            "level": entry.level,
            "message": entry.message,
            "project_id": str(project.id),
            "project_name": project.name,
            "service_label": entry.service_label,
            "error_code": entry.error_code,
            "fingerprint": fp,
            "module": entry.module,
            "function": entry.function,
            "request_id": entry.request_id,
            **(entry.extra or {}),
        }
        file_logger.info(json.dumps(record, ensure_ascii=False, default=str))

    # 3. Flush entries to DB so COUNT queries in alert checks see them
    await db.flush()

    # 4. Run alert checks per entry
    for entry, orm_entry in zip(body.entries, orm_entries):
        await _check_and_fire_alerts(
            db=db,
            project_id=str(project.id),
            entry=entry,
            fingerprint=orm_entry.fingerprint,
        )

    return LogResponse(accepted=len(body.entries))


@router.get("", response_model=list[LogEntryOut])
async def query_logs(
    current_user: CurrentUser,
    db: DB,
    project_id: str = Query(...),
    level: str | None = Query(None),
    service_label: str | None = Query(None),
    error_code: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
) -> list[LogEntryOut]:
    """
    Query stored log entries for a project.
    The project must belong to the authenticated user.
    """

    # Verify ownership
    proj_result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == current_user.id,
            Project.is_active.is_(True),
        )
    )
    if proj_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    q = select(LogEntry).where(LogEntry.project_id == project_id)

    if level:
        q = q.where(LogEntry.level == level)
    if service_label:
        q = q.where(LogEntry.service_label == service_label)
    if error_code:
        q = q.where(LogEntry.error_code == error_code)

    q = q.order_by(LogEntry.ts.desc()).limit(limit).offset(offset)

    result = await db.execute(q)
    entries = result.scalars().all()

    return [
        LogEntryOut(
            id=e.id,
            ts=e.ts,
            level=e.level,
            message=e.message,
            service_label=e.service_label,
            error_code=e.error_code,
            module=e.module,
            function=e.function,
            request_id=e.request_id,
            extra=e.extra,
            fingerprint=e.fingerprint,
            created_at=e.created_at.isoformat(),
        )
        for e in entries
    ]


@router.get("/tail", response_model=list[dict])
async def tail_logs(
    current_user: CurrentUser,
    lines: int = 50,
) -> list[dict]:
    """Return last N lines from the NDJSON file. Dev only."""

    settings = get_settings()

    if settings.environment.value == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Log tail is disabled in production",
        )

    log_path = Path(settings.log_file_path)
    if not log_path.exists():
        return []

    with log_path.open("r", encoding="utf-8") as f:
        all_lines = f.readlines()

    result: list[dict] = []
    for raw in all_lines[-lines:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            result.append(json.loads(raw))
        except json.JSONDecodeError:
            result.append({"raw": raw})

    return result