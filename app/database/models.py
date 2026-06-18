"""SQLAlchemy ORM models.

Tables
------
subscription_plans   – catalogue of plans (free / pro / enterprise …)
users                – registered accounts
user_subscriptions   – which plan a user is on (one active row per user)
projects             – projects owned by a user
log_entries          – structured log entries ingested from SDK
alert_rules          – user-configured alert rules per project
alert_events         – fired alert history (used for cooldown dedup)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Float,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Subscription plans
# ---------------------------------------------------------------------------

class SubscriptionPlan(Base):
    """Catalogue entry for a subscription tier."""

    __tablename__ = "subscription_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    max_projects: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    features: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    subscriptions: Mapped[list[UserSubscription]] = relationship(back_populates="plan")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    subscription: Mapped[UserSubscription | None] = relationship(
        back_populates="user", uselist=False
    )
    projects: Mapped[list[Project]] = relationship(back_populates="owner")


# ---------------------------------------------------------------------------
# User ↔ Plan join (one active row per user)
# ---------------------------------------------------------------------------

class UserSubscription(Base):
    """Tracks which plan a user is currently on."""

    __tablename__ = "user_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_subscription"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscription_plans.id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="subscription")
    plan: Mapped[SubscriptionPlan] = relationship(back_populates="subscriptions")


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    slug: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    api_key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "slug", name="uq_project_owner_slug"),
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    log_entries: Mapped[list[LogEntry]] = relationship(back_populates="project")
    alert_rules: Mapped[list[AlertRule]] = relationship(back_populates="project")


# ---------------------------------------------------------------------------
# Log entries  (persisted from SDK ingest)
# ---------------------------------------------------------------------------

class LogEntry(Base):
    """
    One log line ingested from the SDK.

    ``service_label`` — the logical service name the SDK caller sets
                        (e.g. "payment-service", "auth-service").
    ``error_code``    — optional machine-readable code the SDK caller sets
                        (e.g. "ERR_PAYMENT_API", "ERR_DB_CONN").
    ``fingerprint``   — short hash used for alert dedup bucketing.
                        Computed as sha256(project_id:service_label:level:error_code_or_message_prefix)[:16]
    """

    __tablename__ = "log_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Core log fields
    ts: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # SDK-provided context for alert matching
    service_label: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Optional SDK fields
    module: Mapped[str | None] = mapped_column(String(256), nullable=True)
    function: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Dedup / alert bucketing key — indexed for fast window COUNT queries
    fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    project: Mapped[Project] = relationship(back_populates="log_entries")


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------

class AlertRule(Base):
    """
    A user-configured rule that fires an email when a matching log pattern
    exceeds ``threshold`` occurrences within ``window_seconds``.

    Match logic
    -----------
    ``match_field`` picks which log field to inspect:
        "level"      → log.level == match_value   (e.g. "error")
        "error_code" → log.error_code == match_value  (exact)
        "message"    → match_value in log.message  (substring)

    ``service_label`` scopes the rule to a specific service.
    If None the rule applies to all services in the project.

    Cooldown
    --------
    After an alert fires, it will not re-fire until ``window_seconds``
    have passed (one alert per window per rule).
    """

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False)

    # Scope — if None → match all services in the project
    service_label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # What to match
    match_field: Mapped[str] = mapped_column(String(32), nullable=False)   # "level" | "error_code" | "message"
    match_value: Mapped[str] = mapped_column(String(256), nullable=False)

    # Thresholds
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)

    # Where to send the alert
    notify_email: Mapped[str] = mapped_column(String(320), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    project: Mapped[Project] = relationship(back_populates="alert_rules")
    alert_events: Mapped[list[AlertEvent]] = relationship(back_populates="rule")


# ---------------------------------------------------------------------------
# Alert events  (fired alert history — used for cooldown)
# ---------------------------------------------------------------------------

class AlertEvent(Base):
    """
    Records every time an alert rule fires.
    Used to enforce the one-alert-per-window cooldown:
    before firing, we check if any AlertEvent for the same rule exists
    within the last window_seconds.
    """

    __tablename__ = "alert_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    rule_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    rule: Mapped[AlertRule] = relationship(back_populates="alert_events")