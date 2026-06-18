import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.core.config import get_settings
from app.database.db import create_all, AsyncSessionLocal, seed_plans

from app.router import authrouter, projectrouter, logrouter, subscriptionrouter, alertrouter

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='app.log',
    filemode='a'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup: creating database tables")
    await create_all()
    async with AsyncSessionLocal() as session:
        logger.debug("Seeding subscription plans")
        await seed_plans(session)
    yield
    logger.info("Application shutdown")


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/info")
async def info() -> dict[str, str]:
    return {"name": settings.app_name, "version": settings.app_version}


app.include_router(subscriptionrouter)
app.include_router(authrouter)
app.include_router(projectrouter)
app.include_router(logrouter)
app.include_router(alertrouter)