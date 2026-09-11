from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="UPM_SIGNAGE_", extra="ignore", env_ignore_empty=True
    )
    database_url: str
    site_url: str | None = None
    site_credential: str | None = None
    event_id: UUID | None = None
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: Annotated[str, Field(min_length=1, max_length=1024)] = "admin"
    session_cookie_secure: bool = True
    session_hours: Annotated[int, Field(ge=1, le=168)] = 12
    media_root: str = "/media"
    poll_seconds: Annotated[float, Field(ge=1, le=300)] = 10
    lease_seconds: Annotated[int, Field(ge=10, le=600)] = 60

    @field_validator("database_url")
    @classmethod
    def postgres(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Signage database must use PostgreSQL with psycopg")
        return value
