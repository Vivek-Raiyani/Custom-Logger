"""Authentication endpoints: register + login + me."""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DB
from app.core.security import create_access_token, hash_password, verify_password
from app.database.models import SubscriptionPlan, User, UserSubscription

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    plan: str | None = None
    plan_display_name: str | None = None
    max_projects: int | None = None

    model_config = {"from_attributes": True}


class RegisterResponse(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: DB) -> RegisterResponse:
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    plan_result = await db.execute(
        select(SubscriptionPlan).where(
            SubscriptionPlan.name == "free",
            SubscriptionPlan.is_active == True,  # noqa: E712
        )
    )
    free_plan = plan_result.scalar_one_or_none()
    if free_plan is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Subscription plans not seeded. Run startup.",
        )

    user = User(email=body.email, hashed_password=hash_password(body.password))
    db.add(user)
    await db.flush()

    subscription = UserSubscription(user_id=user.id, plan_id=free_plan.id)
    db.add(subscription)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(subject=user.id)
    return RegisterResponse(
        user=UserOut(
            id=user.id,
            email=user.email,
            plan=free_plan.name,
            plan_display_name=free_plan.display_name,
            max_projects=free_plan.max_projects,
        ),
        access_token=token,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DB) -> TokenResponse:
    result = await db.execute(
        select(User)
        .where(User.email == body.email, User.is_active == True)  # noqa: E712
        .options(selectinload(User.subscription).selectinload(UserSubscription.plan))
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser) -> UserOut:
    """Return the authenticated user's profile and plan info."""
    sub = current_user.subscription
    plan = sub.plan if sub else None

    return UserOut(
        id=current_user.id,
        email=current_user.email,
        plan=plan.name if plan else None,
        plan_display_name=plan.display_name if plan else None,
        max_projects=plan.max_projects if plan else None,
    )