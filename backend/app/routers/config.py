from fastapi import APIRouter
from app.config import settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_frontend_config():
    """Returns public frontend configuration (no secrets)."""
    return {
        "openaip_api_key": settings.openaip_api_key or "",
    }
