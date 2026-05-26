"""
Planey Configuration Module

Loads settings from environment variables with sensible defaults.
"""

from pydantic import Field
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Database ---
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "planey"
    db_user: str = "planey"
    db_password: str = "changeme"

    # --- OpenSky Network ---
    opensky_username: Optional[str] = Field(None, alias="OPENSKY_USERNAME")
    opensky_password: Optional[str] = Field(None, alias="OPENSKY_PASSWORD")

    # --- Home Assistant ---
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: Optional[str] = None
    ha_enabled: bool = False

    # --- Advanced Scraping ---
    flaresolverr_url: str = Field("http://flaresolverr:8191", alias="FLARESOLVERR_URL")

    # --- Application ---
    secret_key: str = "change-this-to-a-random-string"
    polling_interval_seconds: int = 60
    polling_interval_passive_seconds: int = 300
    schedule_sync_interval_minutes: int = 30
    position_retention_days: int = 90
    log_level: str = "INFO"
    
    # --- Webhooks ---
    webhook_token: Optional[str] = None

    # --- Aviation Chart Overlays ---
    openaip_api_key: Optional[str] = Field(None, alias="OPENAIP_API_KEY")

    @property
    def database_url(self) -> str:
        """Construct async PostgreSQL connection URL."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        """Construct sync PostgreSQL connection URL (for Alembic)."""
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
