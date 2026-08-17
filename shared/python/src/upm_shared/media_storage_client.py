"""Synchronous internal client for a deployment-local Media Storage service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import httpx


class MediaStorageUnavailable(RuntimeError):
    """The local storage-management boundary could not serve the operation."""


class MediaStorageOperationError(RuntimeError):
    def __init__(self, message: str, status_code: int, code: str = "storage_error"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class MediaStorageClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.timeout = timeout

    def request(self, method: str, path: str) -> dict:
        try:
            response = httpx.request(
                method, f"{self.base_url}{path}", headers=self.headers, timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise MediaStorageUnavailable("Media Storage service is unavailable.") from error

    def targets(self) -> list[dict]:
        return self.request("GET", "/api/v1/storage/targets")["targets"]

    def assignments(self) -> dict:
        return self.request("GET", "/api/v1/storage/assignments")

    def test(self, target_id: UUID | str) -> dict:
        return self.request("POST", f"/api/v1/storage/targets/{target_id}/test")

    def activate(self, role: str, target_id: UUID | str) -> dict:
        return self.request("PUT", f"/api/v1/storage/assignments/{role}/{target_id}")

    def read_object(self, target_id: UUID | str, key: str, offset: int, limit: int) -> bytes:
        try:
            response = httpx.get(
                f"{self.base_url}/api/v1/storage/objects/{target_id}/{key}",
                headers=self.headers,
                params={"offset": offset, "limit": limit},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as error:
            raise MediaStorageUnavailable("Media Storage service is unavailable.") from error

    def allocate_staging(self) -> dict:
        return self.request("POST", "/api/v1/storage/staging/allocations")

    def append_staging(self, target_id: UUID | str, key: str, offset: int, content: bytes) -> dict:
        try:
            response = httpx.patch(
                f"{self.base_url}/api/v1/storage/staging/{target_id}/{key}",
                headers=self.headers,
                params={"offset": offset},
                content=content,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as error:
            raise MediaStorageUnavailable("Media Storage service is unavailable.") from error

    def commit(self, target_id: UUID | str, key: str, sha256: str) -> dict:
        return self.request_with_json(
            "POST",
            "/api/v1/storage/objects/commit",
            {"staging_target_id": str(target_id), "staging_key": key, "sha256": sha256},
        )

    def release_staging(self, target_id: UUID | str, key: str) -> None:
        self.request("DELETE", f"/api/v1/storage/staging/{target_id}/{key}")

    def request_with_json(self, method: str, path: str, payload: dict) -> dict:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise MediaStorageUnavailable("Media Storage service is unavailable.") from error


class AsyncMediaStorageClient:
    """Bounded-memory byte operations against the local storage service."""

    def __init__(self, base_url: str, token: str, timeout: float = 300):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.timeout = timeout

    async def request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method, f"{self.base_url}{path}", headers=self.headers, **kwargs
                )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            detail = "Media Storage service is unavailable."
            code = "storage_error"
            try:
                payload = error.response.json()
                detail = payload.get("detail", detail)
                code = payload.get("code", code)
            except ValueError:
                pass
            raise MediaStorageOperationError(detail, error.response.status_code, code) from error
        except (httpx.HTTPError, ValueError) as error:
            raise MediaStorageUnavailable("Media Storage service is unavailable.") from error

    async def allocate_staging(self) -> dict:
        return await self.request("POST", "/api/v1/storage/staging/allocations")

    async def write_staging(
        self, target_id: UUID | str, key: str, chunks: AsyncIterator[bytes]
    ) -> dict:
        return await self.request(
            "PUT", f"/api/v1/storage/staging/{target_id}/{key}", content=chunks
        )

    async def commit(self, target_id: UUID | str, key: str, sha256: str) -> dict:
        return await self.request(
            "POST",
            "/api/v1/storage/objects/commit",
            json={"staging_target_id": str(target_id), "staging_key": key, "sha256": sha256},
        )

    async def append_staging(
        self, target_id: UUID | str, key: str, offset: int, chunks: AsyncIterator[bytes]
    ) -> dict:
        return await self.request(
            "PATCH",
            f"/api/v1/storage/staging/{target_id}/{key}",
            params={"offset": offset},
            content=chunks,
        )

    async def release_staging(self, target_id: UUID | str, key: str) -> None:
        await self.request("DELETE", f"/api/v1/storage/staging/{target_id}/{key}")

    async def stream_staging(
        self, target_id: UUID | str, key: str, offset: int, limit: int
    ) -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "GET",
                    f"{self.base_url}/api/v1/storage/staging/{target_id}/{key}",
                    headers=self.headers,
                    params={"offset": offset, "limit": limit},
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        yield chunk
        except httpx.HTTPError as error:
            raise MediaStorageUnavailable("Media Storage service is unavailable.") from error

    async def stream_object(
        self, target_id: UUID | str, key: str, offset: int, limit: int
    ) -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "GET",
                    f"{self.base_url}/api/v1/storage/objects/{target_id}/{key}",
                    headers=self.headers,
                    params={"offset": offset, "limit": limit},
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        yield chunk
        except httpx.HTTPError as error:
            raise MediaStorageUnavailable("Media Storage service is unavailable.") from error
