import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.core.config import get_settings
from app.database.db import create_all, AsyncSessionLocal, seed_plans

from app.router import authrouter, projectrouter, logrouter, subscriptionrouter

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='app.log', 
    filemode='a'
)

logger = logging.getLogger(__name__)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context.

    - Ensure database tables are created
    - Seed development data when appropriate
    - Start background alert worker if enabled
    - Cleanly stop the background worker on shutdown
    """
    logger.info("Application startup: creating database tables")
    await create_all()
    async with AsyncSessionLocal() as session:
        logger.debug("Seeding development data (if enabled)")
        await seed_plans(session)

    # stop_event = asyncio.Event()
    # worker_task = None
    # if settings.alert_worker_enabled:
    #     logger.info("Starting alert worker")
        # worker_task = asyncio.create_task(alert_worker_loop(stop_event))
    yield
    logger.info("Application shutdown: stopping background workers")
    # stop_event.set()
    # if worker_task:
    #     await worker_task


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)



@app.get("/health")
async def health() -> dict[str, str]:
    """Simple health-check endpoint used by load-balancers and containers."""
    logger.debug("Health check requested")
    return {"status": "ok"}

@app.get("/info")
async def info() -> dict[str, str]:
    """Return application information."""
    return {"name": settings.app_name, "version": settings.app_version}



app.include_router(subscriptionrouter)
app.include_router(authrouter)
app.include_router(projectrouter)
app.include_router(logrouter)
