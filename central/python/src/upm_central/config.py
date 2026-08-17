"""Central-only service configuration."""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CentralDatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPM_CENTRAL_", extra="ignore")

    database_url: str
    worker_poll_interval_seconds: Annotated[float, Field(gt=0)] = 1.0
    worker_lease_seconds: Annotated[int, Field(ge=5)] = 60
    worker_retry_base_seconds: Annotated[float, Field(gt=0)] = 5.0
    worker_capabilities: str = "cpu,pdf-conversion,transfer"
    worker_ready_file: str = "/tmp/upm-central-worker-ready"
    admin_token: Annotated[str, Field(min_length=32)]
    bootstrap_admin_username: Annotated[str, Field(min_length=1, max_length=255)] = "admin"
    bootstrap_admin_password: Annotated[str, Field(min_length=1, max_length=1024)] = "admin"
    admin_session_hours: Annotated[int, Field(ge=1, le=168)] = 12
    admin_cookie_secure: bool = False
    credential_issuer_key: Annotated[str, Field(min_length=32)]
    public_url: str = "http://upm-central:8080"
    sync_batch_count: Annotated[int, Field(ge=1, le=100)] = 50
    sync_max_payload_bytes: Annotated[int, Field(ge=1024, le=10_485_760)] = 1_048_576
    media_storage_url: str = "http://central-media-storage:8080"
    media_storage_token: str = ""
    storage_warning_free_percent: Annotated[float, Field(ge=0, le=100)] = 15.0
    storage_critical_free_percent: Annotated[float, Field(ge=0, le=100)] = 5.0
    max_upload_bytes: Annotated[int, Field(gt=0)] = 549_755_813_888
    staging_upload_concurrency: Annotated[int, Field(ge=1, le=64)] = 8
    staging_retry_after_seconds: Annotated[int, Field(ge=1, le=300)] = 2
    operational_log_retention_days: Annotated[int, Field(ge=1, le=3650)] = 30
    transfer_block_bytes: Annotated[int, Field(ge=65_536, le=67_108_864)] = 4_194_304
    transfer_partial_retention_seconds: Annotated[int, Field(ge=3600)] = 604_800

    @field_validator("database_url")
    @classmethod
    def require_psycopg_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Central database_url must use postgresql+psycopg://")
        return value
