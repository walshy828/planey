"""
Planey Database Module

Async SQLAlchemy engine and session management for PostgreSQL.
"""

import asyncio
import logging
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.log_level == "DEBUG",
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency that provides a database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables with retry logic."""
    max_retries = 5
    retry_delay = 5

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Connecting to database (attempt {attempt}/{max_retries})...")
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                # Self-healing database upgrade to add summary_stats column if missing
                await conn.execute(text("ALTER TABLE flights ADD COLUMN IF NOT EXISTS summary_stats JSONB;"))
                # Self-healing: add fa_flight_id for schedule deduplication
                await conn.execute(text("ALTER TABLE flights ADD COLUMN IF NOT EXISTS fa_flight_id VARCHAR(100);"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_flights_fa_flight_id ON flights(fa_flight_id);"))
                # Self-healing: add category column to aircraft table if missing
                await conn.execute(text("ALTER TABLE aircraft ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT 'plane';"))
                
                # Self-healing: ensure flight_change_history table exists
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS flight_change_history (
                        id BIGSERIAL PRIMARY KEY,
                        flight_id UUID NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
                        changed_at TIMESTAMPTZ DEFAULT NOW(),
                        change_source VARCHAR(50) NOT NULL,
                        field_name VARCHAR(50) NOT NULL,
                        old_value TEXT,
                        new_value TEXT
                    );
                """))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_flight_change_history_flight_id ON flight_change_history(flight_id);"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_flight_change_history_changed_at ON flight_change_history(changed_at);"))
                
                # Delete existing orphan positions to allow setting the non-nullable FK constraint
                await conn.execute(text("DELETE FROM positions WHERE flight_id IS NULL;"))
                
                # Enforce the non-nullable FK constraint on flight_id
                await conn.execute(text("ALTER TABLE positions ALTER COLUMN flight_id SET NOT NULL;"))
            logger.info("Database initialized successfully")
            return
        except Exception as e:
            if attempt == max_retries:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Database connection failed: {e}. Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay *= 2

