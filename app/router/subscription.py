"""Subscription plan endpoints"""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import Any

from app.core.deps import DB
from app.core.security import create_access_token, hash_password, verify_password
from app.database.models import SubscriptionPlan, User, UserSubscription


router = APIRouter(prefix="/subscription", tags=["subscription"])

@router.get("/subscription-plans")
async def get_subscription_plans(db:DB) -> list[dict[str, Any]]:
    """Return list of subscription plans."""
    result = await db.execute(select(SubscriptionPlan))
    plans = result.scalars().all()
    return [{"name": plan.name, "price": plan.price} for plan in plans]