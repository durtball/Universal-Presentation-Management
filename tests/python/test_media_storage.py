"""Filesystem-only tests for Site media path, health, and admission rules."""

import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from upm_shared.enums import MediaCategory, StorageHealth, StorageType
from upm_site.media.storage import (
    InsufficientCapacityError,
    StorageError,
    UnsafePathError,
    atomic_finalize,
    generate_object_key,
    observe_storage,
    remove_stale_staging_files,
    require_capacity,
    resolve_object_path,
    staging_path,
    validate_original_filename,
)
from upm_site.persistence.models import StorageTarget


def target(root: Path, **overrides: object) -> StorageTarget:
    values = {
        "storage_target_id": uuid4(),
        "site_id": uuid4(),
        "display_name": "test",
        "storage_type": StorageType.LOCAL_FILESYSTEM,
        "root_path": str(root),
        "enabled": True,
        "primary_media": True,
        "health": StorageHealth.UNKNOWN,
        "warning_free_bytes": None,
        "critical_free_bytes": None,
        "safety_reserve_bytes": 0,
    }
    values.update(overrides)
    return StorageTarget(**values)


def test_object_key_generation_is_safe_and_filename_independent() -> None:
    media_id = uuid4()
    key = generate_object_key(MediaCategory.OPEN_FILE, media_id)

    assert key.startswith("open-files/")
    assert key.endswith(str(media_id))
    assert "speaker deck" not in key


@pytest.mark.parametrize("value", ["../escape", "/absolute", "a\\..\\escape", "a/../b"])
def test_path_resolution_rejects_traversal(tmp_path: Path, value: str) -> None:
    with pytest.raises((ValueError, UnsafePathError)):
        resolve_object_path(target(tmp_path), value)


@pytest.mark.parametrize(
    "value", ["../deck.pptx", "folder/deck.pptx", "folder\\deck.pptx", "bad\x00name"]
)
def test_original_filename_rejects_path_and_control_injection(value: str) -> None:
    with pytest.raises(ValueError):
        validate_original_filename(value)


def test_resolution_stays_under_configured_target(tmp_path: Path) -> None:
    resolved = resolve_object_path(target(tmp_path), "open-files/2026/08/object")

    assert resolved == tmp_path / "open-files" / "2026" / "08" / "object"


def test_resolution_rejects_symlink_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "open-files").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError):
        from upm_site.media.storage import ensure_safe_parent

        ensure_safe_parent(target(tmp_path), "open-files/2026/object")


def test_atomic_finalization_never_overwrites(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    destination = tmp_path / "final"
    staged.write_bytes(b"new")
    destination.write_bytes(b"original")

    with pytest.raises(StorageError):
        atomic_finalize(staged, destination)

    assert destination.read_bytes() == b"original"
    assert staged.read_bytes() == b"new"


def test_capacity_threshold_rejection(tmp_path: Path) -> None:
    observation = observe_storage(target(tmp_path))
    assert observation.free_bytes is not None
    constrained = target(tmp_path, safety_reserve_bytes=observation.free_bytes)

    with pytest.raises(InsufficientCapacityError):
        require_capacity(constrained, 1)


def test_missing_target_is_unavailable(tmp_path: Path) -> None:
    observation = observe_storage(target(tmp_path / "missing"))

    assert observation.available is False
    assert observation.health == StorageHealth.UNAVAILABLE


def test_unwritable_target_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "access", lambda *_args: False)

    observation = observe_storage(target(tmp_path))

    assert observation.available is False
    assert observation.writable is False
    assert observation.health == StorageHealth.READ_ONLY


def test_stale_cleanup_preserves_active_uploads(tmp_path: Path) -> None:
    configured = target(tmp_path)
    active_id = uuid4()
    stale_id = uuid4()
    active = staging_path(configured, active_id)
    stale = staging_path(configured, stale_id)
    active.write_bytes(b"active")
    stale.write_bytes(b"stale")

    removed = remove_stale_staging_files(
        configured,
        active_media_ids={active_id},
        older_than=timedelta(seconds=0),
    )

    assert stale in removed
    assert active.exists()
    assert not stale.exists()
