"""Site-only service configuration."""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SiteSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPM_SITE_", extra="ignore")

    database_url: str
    media_mount_path: str = "/data/objects"
    staging_mount_path: str = "/data/staging"
    storage_warning_free_percent: Annotated[float, Field(ge=0, le=100)] = 15.0
    storage_critical_free_percent: Annotated[float, Field(ge=0, le=100)] = 5.0
    max_upload_bytes: Annotated[int, Field(gt=0)] = 549_755_813_888
    staging_max_age_seconds: Annotated[int, Field(ge=300)] = 86_400
    worker_poll_interval_seconds: Annotated[float, Field(gt=0)] = 1.0
    worker_lease_seconds: Annotated[int, Field(ge=5)] = 60
    worker_retry_base_seconds: Annotated[float, Field(gt=0)] = 5.0
    worker_capabilities: str = "cpu,pdf-conversion,transfer"
    worker_ready_file: str = "/tmp/upm-site-worker-ready"
    credential_encryption_key: Annotated[str, Field(min_length=32)] | None = None
    default_display_name: str = "UPM Site"
    application_version: str = "0.1.0"
    central_url: str | None = None
    sync_batch_count: Annotated[int, Field(ge=1, le=100)] = 50
    sync_max_payload_bytes: Annotated[int, Field(ge=1024, le=10_485_760)] = 1_048_576
    heartbeat_interval_seconds: Annotated[float, Field(gt=0)] = 30.0
    transfer_block_bytes: Annotated[int, Field(ge=65_536, le=67_108_864)] = 4_194_304
    transfer_pull_concurrency: Annotated[int, Field(ge=1, le=16)] = 1
    transfer_push_concurrency: Annotated[int, Field(ge=1, le=16)] = 1
    transfer_partial_retention_seconds: Annotated[int, Field(ge=3600)] = 604_800

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

    @field_validator("staging_mount_path")
    @classmethod
    def require_absolute_staging_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("staging_mount_path must be an absolute Linux container path")
        return value.rstrip("/") or "/"
