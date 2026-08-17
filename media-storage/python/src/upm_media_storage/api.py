"""Private authenticated HTTP API for deployment-local media bytes."""

from __future__ import annotations

import os
import shutil
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from upm_media_storage.config import Settings
from upm_media_storage.service import StorageError, StorageService


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staging_target_id: UUID
    staging_key: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings()
    storage = StorageService(configured)
    app = FastAPI(title="UPM Media Storage", version="1.0.0")

    def authenticated(authorization: str | None = Header(default=None)) -> None:
        if authorization != f"Bearer {configured.service_token}":
            raise HTTPException(401, "invalid media storage service credential")

    private = [Depends(authenticated)]

    @app.exception_handler(StorageError)
    async def storage_error(_request: Request, error: StorageError):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(error), "code": "storage_error"}, status_code=422)

    @app.exception_handler(OSError)
    async def filesystem_error(_request: Request, error: OSError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {
                "detail": "The selected storage target could not persist the upload.",
                "code": "storage_write_error",
            },
            status_code=507,
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "deployment_context": configured.deployment_context}

    @app.get("/api/v1/storage/targets", dependencies=private)
    def targets() -> dict:
        return {"targets": [storage.payload(item) for item in storage.targets.values()]}

    @app.post("/api/v1/storage/targets/{target_id}/test", dependencies=private)
    def test_target(target_id: UUID) -> dict:
        return storage.payload(storage.target(target_id), probe=True)

    @app.get("/api/v1/storage/assignments", dependencies=private)
    def assignments() -> dict:
        return {role: storage.payload(storage.active(role)) for role in ("staging", "media")}

    @app.put("/api/v1/storage/assignments/{role}/{target_id}", dependencies=private)
    def activate(role: str, target_id: UUID) -> dict:
        if role not in {"staging", "media"}:
            raise HTTPException(404, "unknown storage role")
        return storage.payload(storage.activate(role, target_id))

    @app.post("/api/v1/storage/staging/allocations", dependencies=private)
    def allocate() -> dict:
        target = storage.active("staging")
        key = f"staging/{uuid4()}.upload"
        return {
            "storage_target_id": str(target.storage_target_id),
            "storage_key": key,
            "name": target.name,
            "internal_path": str(target.internal_path),
        }

    @app.put("/api/v1/storage/staging/{target_id}/{key:path}", dependencies=private)
    async def write_staging(target_id: UUID, key: str, request: Request) -> dict:
        target = storage.target(target_id)
        if "staging" not in target.roles:
            raise StorageError("Target cannot store staged data.")
        observation = storage.observe(target, probe=True)
        if not observation.writable:
            raise StorageError(
                observation.detail or f'Storage target "{target.name}" is not writable.'
            )
        path = storage.path(target, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4()}.partial")
        size = 0
        try:
            with temporary.open("xb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "storage_target_id": str(target_id),
            "storage_key": key,
            "size_bytes": size,
            "sha256": storage.sha256(path),
        }

    @app.get("/api/v1/storage/staging/{target_id}/{key:path}", dependencies=private)
    def staged(
        target_id: UUID,
        key: str,
        offset: int = Query(0, ge=0),
        limit: int | None = Query(None, ge=1, le=67_108_864),
    ):
        path = storage.path(storage.target(target_id), key)
        if not path.is_file():
            raise HTTPException(404, "staged object not found")
        size = path.stat().st_size
        if offset > size:
            raise HTTPException(416, "offset exceeds staged object size")
        if offset == 0 and limit is None:
            return FileResponse(path)
        count = min(limit or size, size - offset)

        def content():
            with path.open("rb") as source:
                source.seek(offset)
                remaining = count
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            content(), media_type="application/octet-stream", headers={"Content-Length": str(count)}
        )

    @app.patch("/api/v1/storage/staging/{target_id}/{key:path}", dependencies=private)
    async def append_staging(
        target_id: UUID, key: str, request: Request, offset: int = Query(ge=0)
    ) -> dict:
        target = storage.target(target_id)
        path = storage.path(target, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.stat().st_size if path.exists() else 0
        if current != offset:
            raise HTTPException(409, detail={"confirmed_offset": current})
        written = 0
        with path.open("ab", buffering=0) as partial:
            async for chunk in request.stream():
                written += len(chunk)
                partial.write(chunk)
            partial.flush()
            os.fsync(partial.fileno())
        return {
            "storage_target_id": str(target_id),
            "storage_key": key,
            "confirmed_offset": current + written,
        }

    @app.delete("/api/v1/storage/staging/{target_id}/{key:path}", dependencies=private)
    def release(target_id: UUID, key: str) -> dict:
        storage.path(storage.target(target_id), key).unlink(missing_ok=True)
        return {"released": True}

    @app.post("/api/v1/storage/objects/commit", dependencies=private)
    def commit(payload: CommitRequest) -> dict:
        source = storage.path(storage.target(payload.staging_target_id), payload.staging_key)
        if not source.is_file() or storage.sha256(source) != payload.sha256:
            raise StorageError("Staged object is missing or failed SHA-256 verification.")
        target = storage.active("media")
        key = f"objects/sha256/{payload.sha256[:2]}/{payload.sha256}"
        destination = storage.path(target, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_name(f".{destination.name}.{uuid4()}.partial")
            shutil.copyfile(source, temporary)
            if storage.sha256(temporary) != payload.sha256:
                temporary.unlink(missing_ok=True)
                raise StorageError("Committed object failed SHA-256 verification.")
            os.link(temporary, destination)
            temporary.unlink(missing_ok=True)
        return {
            "storage_target_id": str(target.storage_target_id),
            "storage_key": key,
            "name": target.name,
            "internal_path": str(target.internal_path),
            "sha256": payload.sha256,
            "size_bytes": destination.stat().st_size,
        }

    @app.get("/api/v1/storage/objects/{target_id}/{key:path}", dependencies=private)
    def read_object(
        target_id: UUID,
        key: str,
        offset: int = Query(0, ge=0),
        limit: int | None = Query(None, ge=1, le=67_108_864),
    ):
        path = storage.path(storage.target(target_id), key)
        if not path.is_file():
            raise HTTPException(404, "media object not found")
        size = path.stat().st_size
        if offset > size:
            raise HTTPException(416, "offset exceeds media object size")
        if offset == 0 and limit is None:
            return FileResponse(path)
        count = min(limit or size, size - offset)

        def content():
            with path.open("rb") as source:
                source.seek(offset)
                remaining = count
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            content(), media_type="application/octet-stream", headers={"Content-Length": str(count)}
        )

    return app


app = create_app()
