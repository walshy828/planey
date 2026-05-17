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
    # 1. Look up existing manual_airborne_mode in DB to see if it changes from false to true
    manual_airborne_was_enabled = False
    result_mode = await db.execute(select(Setting).where(Setting.key == "manual_airborne_mode"))
    mode_setting = result_mode.scalars().first()
    if mode_setting:
        manual_airborne_was_enabled = mode_setting.value == "true"
        
    for key, value in update.settings.items():
        # Check if exists
        result = await db.execute(select(Setting).where(Setting.key == key))
        setting = result.scalars().first()
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
            
    # 2. If manual_airborne_mode is being turned to true and wasn't before, set set_at timestamp
    if update.settings.get("manual_airborne_mode") == "true" and not manual_airborne_was_enabled:
        from datetime import datetime, timezone
        timestamp_str = datetime.now(timezone.utc).isoformat()
        
        result_ts = await db.execute(select(Setting).where(Setting.key == "manual_airborne_mode_set_at"))
        ts_setting = result_ts.scalars().first()
        if ts_setting:
            ts_setting.value = timestamp_str
        else:
            db.add(Setting(key="manual_airborne_mode_set_at", value=timestamp_str))
            
    # 3. If manual_airborne_mode is being turned to false, clear set_at timestamp
    elif update.settings.get("manual_airborne_mode") == "false":
        result_ts = await db.execute(select(Setting).where(Setting.key == "manual_airborne_mode_set_at"))
        ts_setting = result_ts.scalars().first()
        if ts_setting:
            ts_setting.value = ""
    
    await db.commit()
    
    # 4. Trigger dynamic tracker reschedule
    try:
        from app.services.tracker import tracker_service
        await tracker_service.update_tracker_polling_interval(db)
    except Exception as e:
        print(f"Failed to dynamically adjust tracker polling interval on settings save: {e}")

    return {"status": "success"}

@router.post("/reconcile")
async def reconcile_all_flights(db: AsyncSession = Depends(get_db)):
    """Run an on-demand reconciliation sweep for all aircraft with open flights."""
    from app.services.reconciliation import reconciliation_service
    try:
        result = await reconciliation_service.reconcile_all_active_flights(db)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reconciliation sweep failed: {e}")

