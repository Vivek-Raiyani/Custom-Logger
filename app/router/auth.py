"""Authentication endpoints: register + login."""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import DB
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
    # Check email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    # Resolve the free plan
    plan_result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "free", SubscriptionPlan.is_active == True)  # noqa: E712
    )
    free_plan = plan_result.scalar_one_or_none()
    if free_plan is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Subscription plans not seeded. Run startup.",
        )

    # Create user + subscription in one transaction
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.flush()  # get user.id without committing

    subscription = UserSubscription(user_id=user.id, plan_id=free_plan.id)
    db.add(subscription)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(subject=user.id)
    return RegisterResponse(
        user=UserOut(id=user.id, email=user.email, plan=free_plan.name),
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


# @router.get("/me", response_model=UserOut)
# async def me(db: DB, current_user: User = None) -> UserOut:
#     """Return the authenticated user's profile."""
#     # Import here to avoid circular — deps imports models, not routers
#     from app.core.deps import get_current_user  # noqa: F401 (used via Depends in practice)

#     # This route is wired with the dependency in main.py instead
#     raise HTTPException(status_code=501, detail="Use the dependency-injected version")