"""Alert rule management endpoints.

Users create rules like:
  "If payment-service logs error_code=ERR_PAYMENT_API more than 3 times
   in 1 hour, email ops@company.com"

One rule = one precise matcher. Users create separate rules for each
distinct error type they want to track — this is intentional so that
"payment API failure" and "payment DB failure" are never conflated.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select

from app.core.deps import CurrentUser, DB
from app.database.models import AlertEvent, AlertRule, Project
from app.services.pendo import track as pendo_track

router = APIRouter(prefix="/alerts", tags=["alerts"])

VALID_MATCH_FIELDS = {"level", "error_code", "message"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AlertRuleCreate(BaseModel):
    name: str
    project_id: str

    # Optional scope — if omitted, rule applies to all services
    service_label: str | None = None

    # What to match
    match_field: str          # "level" | "error_code" | "message"
    match_value: str

    # Thresholds
    threshold: int = 3
    window_seconds: int = 3600

    notify_email: EmailStr

    @field_validator("match_field")
    @classmethod
    def validate_match_field(cls, v: str) -> str:
        if v not in VALID_MATCH_FIELDS:
            raise ValueError(f"match_field must be one of: {', '.join(VALID_MATCH_FIELDS)}")
        return v

    @field_validator("threshold")
    @classmethod
    def validate_threshold(cls, v: int) -> int:
        if v < 1:
            raise ValueError("threshold must be >= 1")
        return v

    @field_validator("window_seconds")
    @classmethod
    def validate_window(cls, v: int) -> int:
        if v < 60:
            raise ValueError("window_seconds must be >= 60")
        return v


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    service_label: str | None = None
    match_field: str | None = None
    match_value: str | None = None
    threshold: int | None = None
    window_seconds: int | None = None
    notify_email: EmailStr | None = None
    is_active: bool | None = None

    @field_validator("match_field")
    @classmethod
    def validate_match_field(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_MATCH_FIELDS:
            raise ValueError(f"match_field must be one of: {', '.join(VALID_MATCH_FIELDS)}")
        return v


class AlertRuleOut(BaseModel):
    id: str
    project_id: str
    name: str
    service_label: str | None
    match_field: str
    match_value: str
    threshold: int
    window_seconds: int
    notify_email: str
    is_active: bool
    created_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, r: AlertRule) -> "AlertRuleOut":
        return cls(
            id=r.id,
            project_id=r.project_id,
            name=r.name,
            service_label=r.service_label,
            match_field=r.match_field,
            match_value=r.match_value,
            threshold=r.threshold,
            window_seconds=r.window_seconds,
            notify_email=r.notify_email,
            is_active=r.is_active,
            created_at=r.created_at.isoformat(),
        )


class AlertEventOut(BaseModel):
    id: str
    rule_id: str
    fingerprint: str
    occurrence_count: int
    fired_at: str

    @classmethod
    def from_orm(cls, e: AlertEvent) -> "AlertEventOut":
        return cls(
            id=e.id,
            rule_id=e.rule_id,
            fingerprint=e.fingerprint,
            occurrence_count=e.occurrence_count,
            fired_at=e.fired_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_project_or_403(db, project_id: str, user_id: str) -> Project:
    """Return the project if it belongs to the user, else raise 403/404."""
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == user_id,
            Project.is_active.is_(True),
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


async def _get_rule_or_404(db, rule_id: str, user_id: str) -> AlertRule:
    """Return the rule if it belongs to a project owned by the user."""
    result = await db.execute(
        select(AlertRule)
        .join(Project, Project.id == AlertRule.project_id)
        .where(
            AlertRule.id == rule_id,
            Project.owner_id == user_id,
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")
    return rule


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    body: AlertRuleCreate, current_user: CurrentUser, db: DB
) -> AlertRuleOut:
    """Create a new alert rule for a project."""
    await _get_project_or_403(db, body.project_id, current_user.id)

    rule = AlertRule(
        project_id=body.project_id,
        name=body.name,
        service_label=body.service_label,
        match_field=body.match_field,
        match_value=body.match_value,
        threshold=body.threshold,
        window_seconds=body.window_seconds,
        notify_email=str(body.notify_email),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    await pendo_track(
        "alert_rule_created",
        visitor_id=current_user.id,
        account_id=current_user.id,
        properties={
            "rule_id": rule.id,
            "project_id": rule.project_id,
            "match_field": rule.match_field,
            "match_value": rule.match_value,
            "threshold": rule.threshold,
            "window_seconds": rule.window_seconds,
            "has_service_scope": rule.service_label is not None,
            "service_label": rule.service_label,
        },
    )

    return AlertRuleOut.from_orm(rule)


@router.get("", response_model=list[AlertRuleOut])
async def list_alert_rules(
    current_user: CurrentUser,
    db: DB,
    project_id: str | None = None,
) -> list[AlertRuleOut]:
    """List all alert rules. Optionally filter by project_id."""

    q = (
        select(AlertRule)
        .join(Project, Project.id == AlertRule.project_id)
        .where(Project.owner_id == current_user.id)
        .order_by(AlertRule.created_at.desc())
    )

    if project_id:
        q = q.where(AlertRule.project_id == project_id)

    result = await db.execute(q)
    return [AlertRuleOut.from_orm(r) for r in result.scalars().all()]


@router.get("/{rule_id}", response_model=AlertRuleOut)
async def get_alert_rule(rule_id: str, current_user: CurrentUser, db: DB) -> AlertRuleOut:
    rule = await _get_rule_or_404(db, rule_id, current_user.id)
    return AlertRuleOut.from_orm(rule)


@router.patch("/{rule_id}", response_model=AlertRuleOut)
async def update_alert_rule(
    rule_id: str, body: AlertRuleUpdate, current_user: CurrentUser, db: DB
) -> AlertRuleOut:
    rule = await _get_rule_or_404(db, rule_id, current_user.id)

    changed_fields = []
    if body.name is not None:
        rule.name = body.name
        changed_fields.append("name")
    if body.service_label is not None:
        rule.service_label = body.service_label
        changed_fields.append("service_label")
    if body.match_field is not None:
        rule.match_field = body.match_field
        changed_fields.append("match_field")
    if body.match_value is not None:
        rule.match_value = body.match_value
        changed_fields.append("match_value")
    if body.threshold is not None:
        rule.threshold = body.threshold
        changed_fields.append("threshold")
    if body.window_seconds is not None:
        rule.window_seconds = body.window_seconds
        changed_fields.append("window_seconds")
    if body.notify_email is not None:
        rule.notify_email = str(body.notify_email)
        changed_fields.append("notify_email")
    if body.is_active is not None:
        rule.is_active = body.is_active
        changed_fields.append("is_active")

    await db.commit()
    await db.refresh(rule)

    await pendo_track(
        "alert_rule_updated",
        visitor_id=current_user.id,
        account_id=current_user.id,
        properties={
            "rule_id": rule.id,
            "project_id": rule.project_id,
            "name_changed": "name" in changed_fields,
            "match_field_changed": "match_field" in changed_fields,
            "match_value_changed": "match_value" in changed_fields,
            "threshold_changed": "threshold" in changed_fields,
            "window_seconds_changed": "window_seconds" in changed_fields,
            "notify_email_changed": "notify_email" in changed_fields,
            "is_active_changed": "is_active" in changed_fields,
            "fields_updated_count": len(changed_fields),
        },
    )

    return AlertRuleOut.from_orm(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(rule_id: str, current_user: CurrentUser, db: DB) -> None:
    rule = await _get_rule_or_404(db, rule_id, current_user.id)
    rule_id_val = rule.id
    project_id_val = rule.project_id
    await db.delete(rule)
    await db.commit()

    await pendo_track(
        "alert_rule_deleted",
        visitor_id=current_user.id,
        account_id=current_user.id,
        properties={
            "rule_id": rule_id_val,
            "project_id": project_id_val,
        },
    )


@router.get("/{rule_id}/events", response_model=list[AlertEventOut])
async def list_alert_events(
    rule_id: str,
    current_user: CurrentUser,
    db: DB,
    limit: int = 50,
) -> list[AlertEventOut]:
    """Return the firing history for a rule (most recent first)."""
    await _get_rule_or_404(db, rule_id, current_user.id)

    result = await db.execute(
        select(AlertEvent)
        .where(AlertEvent.rule_id == rule_id)
        .order_by(AlertEvent.fired_at.desc())
        .limit(limit)
    )
    return [AlertEventOut.from_orm(e) for e in result.scalars().all()]