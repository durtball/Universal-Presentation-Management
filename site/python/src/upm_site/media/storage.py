"""Safe logical-path resolution, capacity admission, and storage health."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from upm_shared.domain.media import validate_object_key
from upm_shared.enums import MediaCategory, StorageHealth
from upm_site.persistence.models import StorageTarget

STAGING_DIRECTORY = ".ingestion-staging"
CATEGORY_DIRECTORIES = {
    MediaCategory.PRESENTATION: "presentations",
    MediaCategory.PRESENTATION_VERSION: "presentation-versions",
    MediaCategory.OPEN_FILE: "open-files",
    MediaCategory.DERIVATIVE: "derivatives",
    MediaCategory.PDF_DERIVATIVE: "pdf-derivatives",
    MediaCategory.PREVIEW: "previews",
    MediaCategory.THUMBNAIL: "thumbnails",
    MediaCategory.SIGNAGE: "signage",
    MediaCategory.INGESTION_STAGING: STAGING_DIRECTORY,
    MediaCategory.TEMPORARY_PROCESSING: ".temporary-processing",
    MediaCategory.ARCHIVE: "archive",
}


class StorageError(RuntimeError):
    """Base class for filesystem admission and safety failures."""


class UnsafePathError(StorageError):
    pass


class StorageUnavailableError(StorageError):
    pass


class InsufficientCapacityError(StorageError):
    pass


@dataclass(frozen=True, slots=True)
class StorageObservation:
    storage_target_id: UUID
    observed_at: datetime
    available: bool
    root_exists: bool
    writable: bool
    total_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    health: StorageHealth
    warning_threshold_reached: bool
    critical_threshold_reached: bool
    detail: str | None = None


def validate_original_filename(value: str) -> str:
    """Validate display metadata without using it as filesystem identity."""
    if not value or len(value) > 1024:
        raise ValueError("original filename must contain between 1 and 1024 characters")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("original filename must not contain path components")
    if Path(value).is_absolute() or any(ord(character) < 32 for character in value):
        raise ValueError("original filename contains unsafe characters")
    return value


def generate_object_key(category: MediaCategory, media_object_id: UUID) -> str:
    """Generate a collision-resistant key unrelated to a client filename."""
    directory = CATEGORY_DIRECTORIES[category]
    if directory.startswith("."):
        raise ValueError("temporary categories cannot be authoritative object locations")
    now = datetime.now(UTC)
    return f"{directory}/{now:%Y/%m}/{media_object_id}"


def _resolved_root(target: StorageTarget) -> Path:
    root = Path(target.root_path)
    if not root.is_absolute():
        raise UnsafePathError("storage target root must be absolute")
    try:
        return root.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise StorageUnavailableError("storage target root is unavailable") from error


def resolve_object_path(target: StorageTarget, object_key: str) -> Path:
    """Resolve a validated logical key beneath its configured target root."""
    validate_object_key(object_key)
    root = _resolved_root(target)
    candidate = root.joinpath(*object_key.split("/"))
    resolved_parent = candidate.parent.resolve(strict=False)
    if not resolved_parent.is_relative_to(root):
        raise UnsafePathError("resolved media path escapes the storage target")
    return candidate


def ensure_safe_parent(target: StorageTarget, object_key: str) -> Path:
    """Create parents and reject symlinks between the root and final object."""
    destination = resolve_object_path(target, object_key)
    root = _resolved_root(target)
    relative_parent = destination.parent.relative_to(root)
    current = root
    for part in relative_parent.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            current.mkdir()
            mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise UnsafePathError("media path contains a symlink or non-directory parent")
    return destination


def staging_path(target: StorageTarget, media_object_id: UUID) -> Path:
    return ensure_safe_parent(target, f"{STAGING_DIRECTORY}/{media_object_id}.upload")


def observe_storage(target: StorageTarget, *, verify_write: bool = True) -> StorageObservation:
    """Return an ephemeral health observation; capacity is never persisted as truth."""
    observed_at = datetime.now(UTC)
    if not target.enabled:
        return StorageObservation(
            target.storage_target_id,
            observed_at,
            False,
            Path(target.root_path).exists(),
            False,
            None,
            None,
            None,
            StorageHealth.UNAVAILABLE,
            False,
            False,
            "storage target is disabled",
        )
    try:
        root = _resolved_root(target)
        usage = shutil.disk_usage(root)
    except StorageError as error:
        return StorageObservation(
            target.storage_target_id,
            observed_at,
            False,
            False,
            False,
            None,
            None,
            None,
            StorageHealth.UNAVAILABLE,
            False,
            False,
            str(error),
        )

    writable = os.access(root, os.W_OK)
    detail = None
    if writable and verify_write:
        probe = root / f".upm-write-probe-{os.getpid()}"
        try:
            descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            probe.unlink()
        except OSError as error:
            writable = False
            detail = f"write probe failed: {error.strerror or type(error).__name__}"
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    critical = target.critical_free_bytes is not None and usage.free <= target.critical_free_bytes
    warning = target.warning_free_bytes is not None and usage.free <= target.warning_free_bytes
    if not writable:
        health = StorageHealth.READ_ONLY
    elif critical:
        health = StorageHealth.CRITICAL
    elif warning:
        health = StorageHealth.WARNING
    else:
        health = StorageHealth.HEALTHY
    return StorageObservation(
        target.storage_target_id,
        observed_at,
        writable,
        True,
        writable,
        usage.total,
        usage.used,
        usage.free,
        health,
        warning,
        critical,
        detail,
    )


def require_capacity(target: StorageTarget, expected_size: int) -> StorageObservation:
    if expected_size < 0:
        raise ValueError("expected size cannot be negative")
    observation = observe_storage(target)
    if not observation.available or observation.free_bytes is None:
        raise StorageUnavailableError(observation.detail or "storage target is not writable")
    required_remaining = max(target.safety_reserve_bytes, target.critical_free_bytes or 0)
    if observation.free_bytes - expected_size < required_remaining:
        raise InsufficientCapacityError(
            "upload would cross the storage target critical threshold or safety reserve"
        )
    return observation


def atomic_finalize(staged: Path, destination: Path) -> None:
    """Publish without replacement using a same-filesystem hard link."""
    if staged.stat().st_dev != destination.parent.stat().st_dev:
        raise StorageError("staging and authoritative destination are on different filesystems")
    try:
        os.link(staged, destination, follow_symlinks=False)
    except FileExistsError as error:
        raise StorageError("authoritative media destination already exists") from error
    except OSError as error:
        raise StorageError("atomic media finalization failed") from error
    staged.unlink()


def remove_stale_staging_files(
    target: StorageTarget,
    *,
    active_media_ids: set[UUID],
    older_than: timedelta,
) -> list[Path]:
    """Remove old inactive artifacts while preserving current uploads."""
    directory = resolve_object_path(target, STAGING_DIRECTORY)
    if not directory.exists():
        return []
    cutoff = datetime.now(UTC).timestamp() - older_than.total_seconds()
    removed: list[Path] = []
    for path in directory.glob("*.upload"):
        try:
            media_id = UUID(path.stem)
            if media_id in active_media_ids or path.stat().st_mtime >= cutoff or path.is_symlink():
                continue
            path.unlink()
            removed.append(path)
        except (OSError, ValueError):
            continue
    return removed
