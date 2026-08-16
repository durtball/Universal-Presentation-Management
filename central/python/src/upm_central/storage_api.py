"""Resilient administrator proxy for the deployment-local Media Storage service."""

from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from upm_shared.media_storage_client import MediaStorageClient, MediaStorageUnavailable


def register_storage_routes(
    app: FastAPI, db: Callable[..., Session], admin: Callable, settings: Callable
) -> None:
    del db  # Storage mechanics and target assignments are owned by the storage service.
    protected = [Depends(admin)]

    def client() -> MediaStorageClient:
        configured = settings()
        return MediaStorageClient(configured.media_storage_url, configured.media_storage_token)

    def view(role: str, target: dict) -> dict:
        return {
            "storage_target_id": target["storage_target_id"],
            "role": role,
            "display_name": target["name"],
            "backend_type": target["backend_type"],
            "path": target["internal_path"],
            "enabled": target["enabled"],
            "available": target["health"] != "Unavailable",
            "readable": target["readable"],
            "writable": target["writable"],
            "health": target["health"],
            "total_bytes": target["total_bytes"],
            "used_bytes": target["used_bytes"],
            "free_bytes": target["free_bytes"],
            "percent_used": target["percent_used"],
            "last_successful_check_at": (
                target["checked_at"] if target["health"] != "Unavailable" else None
            ),
            "detail": target["detail"],
            "role_compatibility": target["role_compatibility"],
        }

    @app.get("/api/v1/admin/storage", dependencies=protected, tags=["storage"])
    def list_storage() -> dict:
        try:
            storage = client()
            assignments = storage.assignments()
            return {
                "roots": [view(role, target) for role, target in assignments.items()],
                "targets": storage.targets(),
                "service_available": True,
            }
        except MediaStorageUnavailable as error:
            roots = [
                {
                    "storage_target_id": f"unavailable-{role}",
                    "role": role,
                    "display_name": "Media Storage service unavailable",
                    "path": "",
                    "enabled": False,
                    "available": False,
                    "writable": False,
                    "health": "Unavailable",
                    "detail": str(error),
                }
                for role in ("staging", "media")
            ]
            return {
                "roots": roots,
                "targets": [],
                "service_available": False,
                "detail": str(error),
            }

    @app.post("/api/v1/admin/storage/{role}/test", dependencies=protected, tags=["storage"])
    def test_storage(role: str) -> dict:
        try:
            assigned = client().assignments().get(role)
            if assigned is None:
                raise HTTPException(404, "storage role not found")
            return view(role, client().test(assigned["storage_target_id"]))
        except MediaStorageUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.put("/api/v1/admin/storage/{role}/{target_id}", dependencies=protected, tags=["storage"])
    def change_target(role: str, target_id: str) -> dict:
        if role not in {"staging", "media"}:
            raise HTTPException(404, "storage role not found")
        try:
            tested = client().test(target_id)
            if not tested["writable"] or tested["health"] in {"Unavailable", "Critical"}:
                raise HTTPException(507, tested.get("detail") or "Storage target is not usable.")
            return view(role, client().activate(role, target_id))
        except MediaStorageUnavailable as error:
            raise HTTPException(503, str(error)) from error
