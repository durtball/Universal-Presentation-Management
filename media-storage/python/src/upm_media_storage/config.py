"""Configuration for explicitly mounted storage targets."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_target_id: UUID
    name: str = Field(min_length=1, max_length=120)
    internal_path: Path
    roles: set[str]
    enabled: bool = True

    @field_validator("internal_path")
    @classmethod
    def absolute_mount(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("storage target paths must be absolute")
        return value

    @field_validator("roles")
    @classmethod
    def valid_roles(cls, value: set[str]) -> set[str]:
        if not value or not value <= {"staging", "media"}:
            raise ValueError("roles must contain staging and/or media")
        return value


DEFAULT_TARGETS = [
    TargetConfig(
        storage_target_id=UUID("0198b8d0-63e0-7000-8000-000000000004"),
        name="Default Staging Storage",
        internal_path=Path("/storage/staging"),
        roles={"staging"},
    ),
    TargetConfig(
        storage_target_id=UUID("0198b8d0-63e0-7000-8000-000000000002"),
        name="Default Media Storage",
        internal_path=Path("/storage/media"),
        roles={"media"},
    ),
    TargetConfig(
        storage_target_id=UUID("0198b8d0-63e0-7000-8000-000000000001"),
        name="Default Temporary Storage",
        internal_path=Path("/storage/temp"),
        roles={"staging"},
    ),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPM_MEDIA_STORAGE_")

    deployment_context: str = "development"
    service_token: str = "development-only-change-me"
    targets_json: str | None = None
    state_path: Path = Path("/state/assignments.json")
    smb_incoming_path: Path | None = None
    smb_presentations_path: Path | None = None
    warning_free_percent: float = 15
    critical_free_percent: float = 5
    staging_max_age_seconds: int = Field(default=24 * 60 * 60, gt=0)

    def targets(self) -> list[TargetConfig]:
        if not self.targets_json:
            return DEFAULT_TARGETS
        return [TargetConfig.model_validate(item) for item in json.loads(self.targets_json)]
