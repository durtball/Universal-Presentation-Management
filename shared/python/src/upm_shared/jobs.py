"""Language-neutral job values and retry policy shared by Central and Site."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from upm_shared.enums import JobPriority, SourceSystem

PRIORITY_VALUES: dict[JobPriority, int] = {
    JobPriority.CRITICAL: 400,
    JobPriority.HIGH: 300,
    JobPriority.NORMAL: 200,
    JobPriority.LOW: 100,
    JobPriority.OPTIONAL: 0,
}


class JobPayload(BaseModel):
    """Validated, versioned JSON job payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Annotated[int, Field(ge=1)] = 1
    data: dict[str, object] = Field(default_factory=dict)


class OutboxPayload(BaseModel):
    """Validated, versioned JSON outbox payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Annotated[int, Field(ge=1)] = 1
    source_system: SourceSystem
    data: dict[str, object] = Field(default_factory=dict)


def retry_delay(
    attempt_count: int,
    *,
    base_delay_seconds: float,
    maximum_delay_seconds: float = 3600,
    jitter_fraction: float = 0,
    jitter_sample: float = 0.5,
) -> timedelta:
    """Return bounded exponential backoff with optional symmetric jitter.

    ``jitter_sample`` is injectable to keep tests deterministic and must be in [0, 1].
    """
    if attempt_count < 1:
        raise ValueError("attempt_count must be at least 1")
    if base_delay_seconds <= 0 or maximum_delay_seconds <= 0:
        raise ValueError("retry delays must be positive")
    if not 0 <= jitter_fraction <= 1 or not 0 <= jitter_sample <= 1:
        raise ValueError("jitter values must be between 0 and 1")
    delay = min(base_delay_seconds * (2 ** (attempt_count - 1)), maximum_delay_seconds)
    jitter_multiplier = 1 + ((jitter_sample * 2) - 1) * jitter_fraction
    return timedelta(seconds=delay * jitter_multiplier)


def utc_now() -> datetime:
    return datetime.now(UTC)
