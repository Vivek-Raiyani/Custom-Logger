"""
Log ingestion endpoint.

Writes structured log entries to a rotating file.

Each line in the log file is a JSON object (NDJSON format)
so it's easy to tail, grep, or ingest into any log aggregator later.
"""

import hashlib
import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status,
)
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import DB
from app.database.models import Project
from app.database.db import get_db


router = APIRouter(prefix="/logs", tags=["logs"])

_file_logger: logging.Logger | None = None


# ============================================================================
# Logger
# ============================================================================


def _get_file_logger() -> logging.Logger:
    """Lazy-init a rotating-file logger for inbound log entries."""

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
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )

        handler.setFormatter(
            logging.Formatter("%(message)s")
        )

        logger.addHandler(handler)

    _file_logger = logger
    return logger


# ============================================================================
# Schemas
# ============================================================================

LogLevel = Literal[
    "debug",
    "info",
    "warning",
    "error",
    "critical",
]


class LogEntry(BaseModel):
    level: LogLevel = "info"
    message: str

    timestamp: float | None = None

    module: str | None = None
    function: str | None = None

    request_id: str | None = None

    extra: dict | None = None


class LogBatch(BaseModel):
    entries: list[LogEntry]


class LogResponse(BaseModel):
    accepted: int


# ============================================================================
# Dependencies
# ============================================================================


async def get_project_from_api_key(
    db: DB,
    authorization: str = Header(...),
) -> Project:
    """
    Authenticate SDK requests using a project's API key.
    """

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
        )

    token = authorization[7:].strip()

    token_hash = hashlib.sha256(
        token.encode()
    ).hexdigest()

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


@router.post(
    "",
    response_model=LogResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_logs(
    body: LogBatch,
    project: Project = Depends(get_project_from_api_key),
) -> LogResponse:
    """
    Accept a batch of log entries from the SDK.
    """

    if not body.entries:
        return LogResponse(accepted=0)

    logger = _get_file_logger()

    now_ts = datetime.now(
        timezone.utc
    ).timestamp()

    for entry in body.entries:
        record = {
            "ts": entry.timestamp or now_ts,
            "level": entry.level,
            "message": entry.message,
            "project_id": str(project.id),
            "project_name": project.name,
            "module": entry.module,
            "function": entry.function,
            "request_id": entry.request_id,
            **(entry.extra or {}),
        }

        logger.info(
            json.dumps(
                record,
                ensure_ascii=False,
                default=str,
            )
        )

    return LogResponse(
        accepted=len(body.entries)
    )


@router.get(
    "/tail",
    response_model=list[dict],
)
async def tail_logs(
    lines: int = 50,
) -> list[dict]:
    """
    Return the last N log lines.

    Intended for development/testing only.
    """

    settings = get_settings()

    if settings.environment.value == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Log tail is disabled in production",
        )

    log_path = Path(settings.log_file_path)

    if not log_path.exists():
        return []

    with log_path.open(
        "r",
        encoding="utf-8",
    ) as f:
        all_lines = f.readlines()

    result: list[dict] = []

    for raw in all_lines[-lines:]:
        raw = raw.strip()

        if not raw:
            continue

        try:
            result.append(
                json.loads(raw)
            )
        except json.JSONDecodeError:
            result.append(
                {"raw": raw}
            )

    return result