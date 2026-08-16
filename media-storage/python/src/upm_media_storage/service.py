"""Safe filesystem mechanics behind configured target identities."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from upm_media_storage.config import Settings, TargetConfig


class StorageError(RuntimeError):
    """Safe error suitable for an internal API response."""


@dataclass(slots=True)
class Observation:
    health: str
    checked_at: datetime
    readable: bool
    writable: bool
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    percent_used: float | None = None
    detail: str | None = None


class StorageService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.targets = {target.storage_target_id: target for target in settings.targets()}
        self.assignments = self._load_assignments()

    def _load_assignments(self) -> dict[str, str]:
        defaults = {
            role: str(next(t.storage_target_id for t in self.targets.values() if role in t.roles))
            for role in ("staging", "media")
        }
        try:
            import json

            saved = json.loads(self.settings.state_path.read_text())
            for role in defaults:
                candidate = UUID(saved.get(role, ""))
                if candidate in self.targets and role in self.targets[candidate].roles:
                    defaults[role] = str(candidate)
        except (OSError, ValueError, TypeError):
            pass
        return defaults

    def persist(self) -> None:
        import json

        self.settings.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.assignments, sort_keys=True))
        os.replace(temporary, self.settings.state_path)

    def target(self, target_id: UUID) -> TargetConfig:
        target = self.targets.get(target_id)
        if target is None or not target.enabled:
            raise StorageError("Storage target is not configured or enabled.")
        return target

    def observe(self, target: TargetConfig, probe: bool = False) -> Observation:
        now = datetime.now(UTC)
        root = target.internal_path
        try:
            if not root.exists():
                raise StorageError(f'Storage target "{target.name}" is not mounted.')
            if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
                raise StorageError(f'Storage target "{target.name}" is not readable.')
            writable = os.access(root, os.W_OK)
            if probe:
                payload = os.urandom(32)
                path = root / f".upm-probe-{uuid4()}"
                try:
                    with path.open("xb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if path.read_bytes() != payload:
                        raise StorageError(
                            f'Storage target "{target.name}" failed probe read-back.'
                        )
                except OSError as error:
                    raise StorageError(
                        f'Storage target "{target.name}" is not writable.'
                    ) from error
                finally:
                    path.unlink(missing_ok=True)
                writable = True
            usage = shutil.disk_usage(root)
            free_percent = 100 * usage.free / usage.total if usage.total else 0
            health = (
                "Critical"
                if free_percent < self.settings.critical_free_percent
                else ("Warning" if free_percent < self.settings.warning_free_percent else "Healthy")
            )
            if not writable:
                health = "Unavailable"
            return Observation(
                health,
                now,
                True,
                writable,
                usage.total,
                usage.used,
                usage.free,
                100 * usage.used / usage.total if usage.total else 100,
                None if writable else f'Storage target "{target.name}" is not writable.',
            )
        except (OSError, StorageError) as error:
            return Observation("Unavailable", now, False, False, detail=str(error))

    @staticmethod
    def safe_key(key: str) -> PurePosixPath:
        parsed = PurePosixPath(key)
        if not key or parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
            raise StorageError("Invalid storage key.")
        return parsed

    def path(self, target: TargetConfig, key: str) -> Path:
        relative = self.safe_key(key)
        root = target.internal_path.resolve(strict=True)
        result = (root / Path(*relative.parts)).resolve(strict=False)
        if not result.is_relative_to(root):
            raise StorageError("Storage key escapes its configured target.")
        return result

    def active(self, role: str) -> TargetConfig:
        return self.target(UUID(self.assignments[role]))

    def activate(self, role: str, target_id: UUID) -> TargetConfig:
        target = self.target(target_id)
        if role not in target.roles:
            raise StorageError(f'Storage target "{target.name}" is not compatible with {role}.')
        result = self.observe(target, probe=True)
        if result.health in {"Unavailable", "Critical"}:
            raise StorageError(
                result.detail or f'Storage target "{target.name}" has critical capacity.'
            )
        self.assignments[role] = str(target_id)
        self.persist()
        return target

    def sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def payload(self, target: TargetConfig, probe: bool = False) -> dict:
        result = asdict(self.observe(target, probe))
        result["checked_at"] = result["checked_at"].isoformat()
        return {
            "storage_target_id": str(target.storage_target_id),
            "name": target.name,
            "role_compatibility": sorted(target.roles),
            "backend_type": "local_filesystem",
            "internal_path": str(target.internal_path),
            "enabled": target.enabled,
            **result,
        }
