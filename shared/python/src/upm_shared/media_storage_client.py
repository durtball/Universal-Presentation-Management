"""Synchronous internal client for a deployment-local Media Storage service."""

from __future__ import annotations

from uuid import UUID

import httpx


class MediaStorageUnavailable(RuntimeError):
    """The local storage-management boundary could not serve the operation."""


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
