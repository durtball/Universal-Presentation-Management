"""Site-only database and media mount configuration."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SiteSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPM_SITE_", extra="ignore")

    database_url: str
    media_mount_path: str = "/var/lib/upm/media"

    @field_validator("database_url")
    @classmethod
    def require_psycopg_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Site database_url must use postgresql+psycopg://")
        return value

    @field_validator("media_mount_path")
    @classmethod
    def require_absolute_container_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("media_mount_path must be an absolute Linux container path")
        return value.rstrip("/") or "/"
