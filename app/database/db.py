"""Async SQLAlchemy engine, session factory and startup helpers."""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.database.models import Base, SubscriptionPlan

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=(settings.environment == "development"),
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Seed default subscription plans (idempotent)
# ---------------------------------------------------------------------------

_DEFAULT_PLANS = [
    {
        "name": "free",
        "display_name": "Free",
        "price": 0,
        "max_projects": 1,
        "features": {
            "log_retention_days": 1,
            "alerts": False,
            "team_members": 1,
        },
    },
    {
        "name": "starter",
        "display_name": "Starter",
        "price": 10,
        "max_projects": 5,
        "features": {
            "log_retention_days": 7,
            "alerts": True,
            "team_members": 3,
        },
    },
    {
        "name": "pro",
        "display_name": "Pro",
        "price": 20,
        "max_projects": 20,
        "features": {
            "log_retention_days": 30,
            "alerts": True,
            "team_members": 10,
        },
    },
    {
        "name": "enterprise",
        "display_name": "Enterprise",
        "price": -1,
        "max_projects": -1,  # unlimited
        "features": {
            "log_retention_days": 365,
            "alerts": True,
            "team_members": -1,
        },
    },
]


async def seed_plans(session: AsyncSession) -> None:
    """Insert default plans if they don't exist yet."""
    from sqlalchemy import select

    for plan_data in _DEFAULT_PLANS:
        result = await session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.name == plan_data["name"])
        )
        if result.scalar_one_or_none() is None:
            session.add(SubscriptionPlan(**plan_data))

    await session.commit()