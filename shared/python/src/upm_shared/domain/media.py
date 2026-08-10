"""Logical media-location domain rules."""

from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID


def validate_object_key(value: str) -> str:
    """Validate a portable relative media object key."""
    if not value or value.startswith(("/", "\\")):
        raise ValueError("object_key must be a non-empty relative path")
    if "\\" in value:
        raise ValueError("object_key must use forward slashes")

    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("object_key cannot contain empty, current, or parent segments")
    if str(path) != value:
        raise ValueError("object_key must be in canonical relative form")
    return value


@dataclass(frozen=True, slots=True)
class LogicalMediaLocation:
    """A stable location independent of the host mount path."""

    storage_target_id: UUID
    object_key: str

    def __post_init__(self) -> None:
        validate_object_key(self.object_key)
