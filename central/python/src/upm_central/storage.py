"""Deployment-local durable storage configuration and filesystem health."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_central.persistence.models import StorageRoot


class StorageValidationError(RuntimeError):
    """An actionable, operator-safe storage validation failure."""


@dataclass(frozen=True, slots=True)
class StorageHealthResult:
    state: str
    checked_at: datetime
    readable: bool
    writable: bool
    total_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    percent_used: float | None
    detail: str | None


def check_path(
    path: str, *, warning_percent: float = 15, critical_percent: float = 5, create: bool = False
) -> StorageHealthResult:
    checked = datetime.now(UTC)
    root = Path(path)
    try:
        if not root.exists():
            if not create:
                raise StorageValidationError("configured path does not exist or is not mounted")
            root.mkdir(parents=True, mode=0o750)
        if not root.is_dir():
            raise StorageValidationError("configured path is not a directory")
        readable = os.access(root, os.R_OK | os.X_OK)
        if not readable:
            raise StorageValidationError("configured path is not readable")
        probe = root / f".upm-storage-probe-{os.getpid()}-{checked.timestamp():.0f}"
        payload = os.urandom(32)
        try:
            with probe.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if probe.read_bytes() != payload:
                raise StorageValidationError("storage probe read-back failed")
        except OSError as error:
            raise StorageValidationError(
                f"configured path is not writable: {error.strerror or type(error).__name__}"
            ) from error
        finally:
            probe.unlink(missing_ok=True)
        usage = shutil.disk_usage(root)
        free_percent = usage.free * 100 / usage.total if usage.total else 0
        state = (
            "critical"
            if free_percent < critical_percent
            else ("warning" if free_percent < warning_percent else "healthy")
        )
        return StorageHealthResult(
            state,
            checked,
            True,
            True,
            usage.total,
            usage.used,
            usage.free,
            usage.used * 100 / usage.total if usage.total else 100,
            None,
        )
    except StorageValidationError as error:
        return StorageHealthResult(
            "unavailable", checked, False, False, None, None, None, None, str(error)
        )
    except OSError as error:
        return StorageHealthResult(
            "unavailable",
            checked,
            False,
            False,
            None,
            None,
            None,
            None,
            f"filesystem capacity or access check failed: {error.strerror or type(error).__name__}",
        )


def active_root(session: Session, role: str) -> StorageRoot:
    root = session.scalar(
        select(StorageRoot).where(StorageRoot.role == role, StorageRoot.enabled.is_(True))
    )
    if root is None:
        raise StorageValidationError(f"{role.title()} storage has no active configuration")
    return root


def require_healthy(
    root: StorageRoot, *, warning_percent: float = 15, critical_percent: float = 5
) -> Path:
    result = check_path(
        root.path, warning_percent=warning_percent, critical_percent=critical_percent
    )
    if result.state == "unavailable":
        raise StorageValidationError(f"{root.role.title()} storage is unavailable: {result.detail}")
    if result.state == "critical":
        raise StorageValidationError(
            f"{root.role.title()} storage has reached the critical free-space threshold"
        )
    return Path(root.path)
