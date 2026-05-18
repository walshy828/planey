import asyncio
import logging
import sys

# Ensure application modules can be resolved if run as script
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import select
from app.database import async_session
from app.models import Flight
from app.services.stats_calculator import calculate_flight_stats

# Configure logging to output cleanly to standard console output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("backfill_stats")

async def backfill():
    logger.info("Starting one-time backfill process for historical flight statistics...")
    
    async with async_session() as session:
        # Fetch all flights that have completed and landed
        result = await session.execute(
            select(Flight).where(Flight.status == "landed")
        )
        flights = result.scalars().all()
        
        logger.info(f"Found {len(flights)} historical landed flights in the database.")
        
        count = 0
        for flight in flights:
            logger.info(f"Processing flight {flight.id} ({flight.flight_number or flight.callsign or 'Unknown'})...")
            try:
                stats = await calculate_flight_stats(flight, session)
                flight.summary_stats = stats
                logger.info(f"Successfully calculated stats for flight {flight.id}: {stats}")
                count += 1
            except Exception as e:
                logger.error(f"Failed to calculate stats for flight {flight.id}: {e}")
                
        if count > 0:
            await session.commit()
            logger.info(f"Successfully backfilled stats for {count} flights and committed to database.")
        else:
            logger.info("No flights were updated.")

if __name__ == "__main__":
    asyncio.run(backfill())
