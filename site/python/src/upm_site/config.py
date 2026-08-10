"""Site-only service configuration."""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SiteSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPM_SITE_", extra="ignore")

    database_url: str
    media_mount_path: str = "/var/lib/upm/media"
    max_upload_bytes: Annotated[int, Field(gt=0)] = 549_755_813_888
    staging_max_age_seconds: Annotated[int, Field(ge=300)] = 86_400
    worker_poll_interval_seconds: Annotated[float, Field(gt=0)] = 1.0
    worker_lease_seconds: Annotated[int, Field(ge=5)] = 60
    worker_retry_base_seconds: Annotated[float, Field(gt=0)] = 5.0
    worker_capabilities: str = "cpu,pdf-conversion,transfer"
    worker_ready_file: str = "/tmp/upm-site-worker-ready"

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
