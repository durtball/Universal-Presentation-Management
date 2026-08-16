"""Administrator storage status, validation, and safe staging cutover APIs."""

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_central.persistence.models import AuditRecord, PresentationMediaImport, StorageRoot
from upm_central.storage import check_path


class LocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=2048)


def register_storage_routes(
    app: FastAPI, db: Callable[..., Session], admin: Callable, settings: Callable
):
    DbSession = Annotated[Session, Depends(db)]
    protected = [Depends(admin)]

    def ensure_defaults(session: Session) -> list[StorageRoot]:
        configured = settings()
        existing = list(session.scalars(select(StorageRoot).where(StorageRoot.enabled.is_(True))))
        roles = {root.role for root in existing}
        for role, name, path in (
            ("staging", "Temporary / Staging Storage", configured.media_staging_path),
            ("media", "Main Media Storage", configured.media_objects_path),
        ):
            if role not in roles:
                root = StorageRoot(
                    role=role, display_name=name, path=path, backend_type="filesystem", enabled=True
                )
                session.add(root)
                existing.append(root)
        session.flush()
        return sorted(existing, key=lambda item: item.role, reverse=True)

    def view(session: Session, root: StorageRoot, *, create: bool = False) -> dict:
        configured = settings()
        health = check_path(
            root.path,
            warning_percent=configured.storage_warning_free_percent,
            critical_percent=configured.storage_critical_free_percent,
            create=create,
        )
        if health.state != "unavailable":
            root.last_successful_check_at = health.checked_at
        if root.role == "staging":
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(PresentationMediaImport)
                    .where(PresentationMediaImport.staging_storage_root_id == root.storage_root_id)
                )
                or 0
            )
            owned = (
                session.scalar(
                    select(func.coalesce(func.sum(PresentationMediaImport.size_bytes), 0)).where(
                        PresentationMediaImport.staging_storage_root_id == root.storage_root_id
                    )
                )
                or 0
            )
        else:
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(PresentationMediaImport)
                    .where(PresentationMediaImport.presentation_version_id.is_not(None))
                )
                or 0
            )
            owned = (
                session.scalar(
                    select(func.coalesce(func.sum(PresentationMediaImport.size_bytes), 0)).where(
                        PresentationMediaImport.presentation_version_id.is_not(None)
                    )
                )
                or 0
            )
        return {
            "storage_root_id": str(root.storage_root_id),
            "role": root.role,
            "display_name": root.display_name,
            "backend_type": root.backend_type,
            "path": root.path,
            "enabled": root.enabled,
            "last_successful_check_at": root.last_successful_check_at,
            "object_count": count,
            "upm_owned_bytes": owned,
            **asdict(health),
        }

    @app.get("/api/v1/admin/storage", dependencies=protected, tags=["storage"])
    def list_storage(session: DbSession) -> dict:
        roots = ensure_defaults(session)
        result = [view(session, root) for root in roots]
        session.commit()
        return {"roots": result}

    @app.post("/api/v1/admin/storage/{role}/test", dependencies=protected, tags=["storage"])
    def test_storage(role: str, session: DbSession) -> dict:
        root = next((item for item in ensure_defaults(session) if item.role == role), None)
        if root is None:
            raise HTTPException(404, "storage role not found")
        result = view(session, root)
        session.commit()
        return result

    @app.put("/api/v1/admin/storage/staging", dependencies=protected, tags=["storage"])
    def change_staging(payload: LocationRequest, session: DbSession) -> dict:
        if not Path(payload.path).is_absolute():
            raise HTTPException(422, "storage path must be absolute")
        health = check_path(
            payload.path,
            create=True,
            warning_percent=settings().storage_warning_free_percent,
            critical_percent=settings().storage_critical_free_percent,
        )
        if health.state in {"unavailable", "critical"}:
            raise HTTPException(
                status.HTTP_507_INSUFFICIENT_STORAGE,
                health.detail or "destination has reached the critical free-space threshold",
            )
        previous = next(item for item in ensure_defaults(session) if item.role == "staging")
        if previous.path == payload.path:
            return view(session, previous)
        previous.enabled = False
        replacement = StorageRoot(
            role="staging",
            display_name=previous.display_name,
            path=payload.path,
            backend_type="filesystem",
            enabled=True,
            last_successful_check_at=health.checked_at,
        )
        session.add(replacement)
        session.flush()
        session.add(
            AuditRecord(
                actor_id="central-admin",
                action="central.storage.staging_changed",
                target_type="storage_root",
                target_id=replacement.storage_root_id,
                before_context={"path": previous.path},
                after_context={"path": payload.path},
            )
        )
        session.commit()
        return view(session, replacement)
