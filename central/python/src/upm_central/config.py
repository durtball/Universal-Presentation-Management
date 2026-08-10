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

    @field_validator("database_url")
    @classmethod
    def require_psycopg_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Central database_url must use postgresql+psycopg://")
        return value
