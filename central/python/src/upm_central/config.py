"""Central-only database configuration."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CentralDatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPM_CENTRAL_", extra="ignore")

    database_url: str

    @field_validator("database_url")
    @classmethod
    def require_psycopg_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Central database_url must use postgresql+psycopg://")
        return value
