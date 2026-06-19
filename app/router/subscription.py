"""Subscription plan endpoints."""
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DB
from app.database.models import SubscriptionPlan, UserSubscription
from app.services.pendo import track as pendo_track

router = APIRouter(prefix="/subscription", tags=["subscription"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SubscriptionOut(BaseModel):
    plan_name: str
    plan_display_name: str
    price: float
    max_projects: int
    features: dict
    started_at: str
    expires_at: str | None


class UpgradeRequest(BaseModel):
    plan_name: str  # "free" | "starter" | "pro" | "enterprise"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/plans", response_model=list[dict[str, Any]])
async def get_subscription_plans(db: DB) -> list[dict[str, Any]]:
    """Return all active subscription plans."""
    result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.is_active == True)  # noqa: E712
    )
    plans = result.scalars().all()
    return [
        {
            "name": p.name,
            "display_name": p.display_name,
            "price": p.price,
            "max_projects": p.max_projects,
            "features": p.features,
        }
        for p in plans
    ]


@router.get("/me", response_model=SubscriptionOut)
async def get_my_subscription(current_user: CurrentUser, db: DB) -> SubscriptionOut:
    """Return the current user's active subscription."""
    result = await db.execute(
        select(UserSubscription)
        .where(UserSubscription.user_id == current_user.id)
        .options(selectinload(UserSubscription.plan))
    )
    sub = result.scalar_one_or_none()

    if sub is None or sub.plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription found.",
        )

    return SubscriptionOut(
        plan_name=sub.plan.name,
        plan_display_name=sub.plan.display_name,
        price=sub.plan.price,
        max_projects=sub.plan.max_projects,
        features=sub.plan.features,
        started_at=sub.started_at.isoformat(),
        expires_at=sub.expires_at.isoformat() if sub.expires_at else None,
    )


@router.post("/upgrade", response_model=SubscriptionOut)
async def upgrade_subscription(
    body: UpgradeRequest, current_user: CurrentUser, db: DB
) -> SubscriptionOut:
    """
    Switch the current user to a different plan.

    Without payment integration this swaps the plan immediately.
    Plug in Stripe/etc. before this line when ready.
    """
    # Resolve target plan
    plan_result = await db.execute(
        select(SubscriptionPlan).where(
            SubscriptionPlan.name == body.plan_name,
            SubscriptionPlan.is_active == True,  # noqa: E712
        )
    )
    new_plan = plan_result.scalar_one_or_none()
    if new_plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{body.plan_name}' not found or inactive.",
        )

    # Fetch current subscription
    sub_result = await db.execute(
        select(UserSubscription)
        .where(UserSubscription.user_id == current_user.id)
        .options(selectinload(UserSubscription.plan))
    )
    sub = sub_result.scalar_one_or_none()

    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription record found for this user.",
        )

    if sub.plan_id == new_plan.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You are already on the '{new_plan.display_name}' plan.",
        )

    previous_plan_name = sub.plan.name if sub.plan else None

    # Swap the plan
    sub.plan_id = new_plan.id
    # expires_at intentionally left as-is — payment layer will manage this later

    await db.commit()
    await db.refresh(sub)

    await pendo_track(
        "subscription_changed",
        visitor_id=current_user.id,
        account_id=current_user.id,
        properties={
            "previous_plan_name": previous_plan_name,
            "new_plan_name": new_plan.name,
            "new_plan_price": new_plan.price,
            "new_max_projects": new_plan.max_projects,
        },
    )

    return SubscriptionOut(
        plan_name=new_plan.name,
        plan_display_name=new_plan.display_name,
        price=new_plan.price,
        max_projects=new_plan.max_projects,
        features=new_plan.features,
        started_at=sub.started_at.isoformat(),
        expires_at=sub.expires_at.isoformat() if sub.expires_at else None,
    )