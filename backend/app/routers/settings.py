from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Setting
from pydantic import BaseModel
from typing import List, Dict

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingUpdate(BaseModel):
    settings: Dict[str, str]

@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting))
    settings = result.scalars().all()
    # Return as a simple dict
    return {s.key: s.value for s in settings}

@router.post("")
async def update_settings(update: SettingUpdate, db: AsyncSession = Depends(get_db)):
    for key, value in update.settings.items():
        # Check if exists
        result = await db.execute(select(Setting).where(Setting.key == key))
        setting = result.scalars().first()
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
    
    await db.commit()
    
    # Reload settings in the background or notify services
    if "polling_interval_seconds" in update.settings:
        try:
            from app.main import scheduler
            new_interval = int(update.settings["polling_interval_seconds"])
            scheduler.reschedule_job("poll_positions", trigger="interval", seconds=new_interval)
            print(f"Rescheduled poll_positions to {new_interval}s")
        except Exception as e:
            print(f"Failed to reschedule poll_positions: {e}")

    return {"status": "success"}
