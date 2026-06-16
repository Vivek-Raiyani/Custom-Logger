"""SQLAlchemy ORM models.

Tables
------
subscription_plans   – catalogue of plans (free / pro / enterprise …)
users                – registered accounts
user_subscriptions   – which plan a user is on (one active row per user)
projects             – projects owned by a user
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
    """Catalogue entry for a subscription tier.

    ``features`` is a free-form JSON object so you can add flags (e.g.
    ``{"alerts": true, "log_retention_days": 30}``) without schema changes.
    ``max_projects`` of -1 means unlimited.
    """

    __tablename__ = "subscription_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # "free", "pro", …
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
    """Tracks which plan a user is currently on.

    Only one row per user is expected (enforced by the unique constraint on
    ``user_id``).  When upgrading/downgrading, UPDATE the existing row.
    """

    __tablename__ = "user_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_subscription"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscription_plans.id"), nullable=False
    )
    # ISO 8601 date strings are fine; use None for "no expiry" (lifetime / manual)
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

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_new_uuid,
    )

    owner_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False)

    description: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    slug: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        index=True,
    )

    api_key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    api_key_prefix: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "slug",
            name="uq_project_owner_slug",
        ),
    )

    owner: Mapped["User"] = relationship(
        back_populates="projects"
    )