"""
Planey - Flight Tracking & Management Platform

Main FastAPI application entry point.
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import init_db
from app.services.tracker import tracker_service
from app.services.cleanup import cleanup_service
from app.services.websocket import ws_manager

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# APScheduler instance
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    logger.info("=" * 60)
    logger.info("  Planey - Flight Tracking Platform")
    logger.info("=" * 60)

    # Initialize database tables
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized")

    # Fetch polling interval from DB, fallback to settings
    from app.database import async_session
    from sqlalchemy import select
    from app.models import Setting
    
    poll_interval = settings.polling_interval_seconds
    async with async_session() as session:
        res = await session.execute(select(Setting).where(Setting.key == 'polling_interval_seconds'))
        db_setting = res.scalars().first()
        if db_setting and db_setting.value.isdigit():
            poll_interval = int(db_setting.value)

    # Schedule tracking poll
    scheduler.add_job(
        tracker_service.poll_positions,
        "interval",
        seconds=poll_interval,
        id="poll_positions",
        name="Poll OpenSky positions",
        max_instances=1,
    )

    # Schedule daily cleanup (run at 3 AM UTC)
    scheduler.add_job(
        cleanup_service.purge_old_positions,
        "cron",
        hour=3,
        minute=0,
        id="cleanup_positions",
        name="Purge old positions",
    )

    # Schedule flight schedule sync
    scheduler.add_job(
        tracker_service.sync_flight_schedules,
        "interval",
        minutes=settings.schedule_sync_interval_minutes,
        id="sync_flight_schedules",
        name="Sync flight schedules",
    )

    # Schedule stuck flight reconciliation check
    from app.services.reconciliation import reconciliation_service
    scheduler.add_job(
        reconciliation_service.run_reconciliation_job,
        "interval",
        minutes=5,
        id="reconcile_flights",
        name="Reconcile stuck flights",
        max_instances=1,
    )

    # Schedule weekly downsampling (Sunday 4 AM UTC)
    scheduler.add_job(
        cleanup_service.downsample_old_positions,
        "cron",
        day_of_week="sun",
        hour=4,
        minute=0,
        id="downsample_positions",
        name="Downsample old positions",
    )

    scheduler.start()
    logger.info(f"Scheduler started - polling every {poll_interval}s")

    # Trigger background reconciliation sweep on startup
    async def run_startup_reconciliation():
        await asyncio.sleep(5)  # Let application startup fully
        logger.info("Running startup reconciliation sweep...")
        try:
            async with async_session() as session:
                res = await reconciliation_service.reconcile_all_active_flights(session)
                logger.info(f"Startup reconciliation sweep completed: {res}")
        except Exception as startup_err:
            logger.error(f"Failed to run startup reconciliation sweep: {startup_err}")

    asyncio.create_task(run_startup_reconciliation())

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("Planey shutting down")


# Create FastAPI app
app = FastAPI(
    title="Planey",
    description="Flight Tracking & Management Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and mount routers
from app.routers import aircraft, flights, positions, settings as settings_router, webhooks

app.include_router(aircraft.router)
app.include_router(flights.router)
app.include_router(positions.router)
app.include_router(settings_router.router)
app.include_router(webhooks.router)





# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time position and status updates."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, listen for client messages
            data = await websocket.receive_text()
            # Could handle client commands here (e.g., subscribe to specific aircraft)
            logger.debug(f"WS received: {data}")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# Health check
@app.get("/api/health")
async def health_check():
    """Health check endpoint for Docker monitoring."""
    return {
        "status": "healthy",
        "service": "planey-api",
        "version": "1.0.0",
        "websocket_connections": ws_manager.connection_count,
        "ha_enabled": settings.ha_enabled,
        "opensky_authenticated": bool(settings.opensky_username),
    }


# Stats endpoint
@app.get("/api/stats")
async def get_stats():
    """Get system statistics."""
    from sqlalchemy import func, select
    from app.database import async_session
    from app.models import Aircraft, Flight, Position

    async with async_session() as session:
        ac_count = await session.execute(
            select(func.count(Aircraft.id)).where(Aircraft.active == True)
        )
        flight_count = await session.execute(
            select(func.count(Flight.id)).where(Flight.status.in_(["scheduled", "active"]))
        )
        pos_count = await session.execute(
            select(func.count(Position.id))
        )
        total_flights = await session.execute(
            select(func.count(Flight.id))
        )

        return {
            "active_aircraft": ac_count.scalar() or 0,
            "active_flights": flight_count.scalar() or 0,
            "total_positions": pos_count.scalar() or 0,
            "total_flights": total_flights.scalar() or 0,
            "websocket_connections": ws_manager.connection_count,
        }

# Mount static frontend files at the very end
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    logger.info(f"Serving static files from {static_dir}")
