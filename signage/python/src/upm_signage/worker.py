import asyncio
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import delete, select

from .config import Settings
from .db import factory
from .models import Installation, OperatorUser, Projection, Source


def _secret(settings: Settings) -> str | None:
    try:
        value = Path(settings.enrollment_secret_file or "").read_text().strip()
        return value if len(value) >= 32 else None
    except OSError:
        return None


def _cipher(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def installation(sessions) -> Installation:
    with sessions.begin() as session:
        row = session.scalar(select(Installation).where(Installation.singleton_key == 1))
        if row is None:
            row = Installation(singleton_key=1)
            session.add(row)
            session.flush()
        return row


async def enroll(settings: Settings, sessions, *, previous: str | None = None) -> str:
    secret = _secret(settings)
    if not settings.site_url or (not secret and not previous):
        raise RuntimeError("Site enrollment is not available")
    identity = installation(sessions)
    headers = {}
    if secret:
        headers["X-UPM-Signage-Enrollment"] = secret
    if previous:
        headers["Authorization"] = f"Bearer {previous}"
    async with httpx.AsyncClient(base_url=settings.site_url, timeout=15) as client:
        response = await client.post(
            "/api/v1/signage/service/enroll",
            headers=headers,
            json={
                "installation_id": str(identity.installation_id),
                "display_name": settings.installation_name,
                "version": settings.application_version,
            },
        )
        response.raise_for_status()
        body = response.json()
    if not secret:
        raise RuntimeError("Enrollment secret is required to protect the stored credential")
    with sessions.begin() as session:
        row = session.get(Installation, identity.installation_id)
        row.site_id = UUID(body["site_id"])
        row.site_name = body["site_name"]
        row.site_url = settings.site_url
        row.credential_encrypted = _cipher(secret).encrypt(body["credential"].encode())
        row.credential_revision = body["credential_revision"]
        row.enrolled_at = datetime.now(UTC)
    return body["credential"]


async def credential(settings: Settings, sessions) -> str:
    if settings.site_credential:
        return settings.site_credential
    secret = _secret(settings)
    row = installation(sessions)
    current = None
    if secret and row.credential_encrypted:
        current = _cipher(secret).decrypt(row.credential_encrypted).decode()
    if not current or not row.enrolled_at:
        return await enroll(settings, sessions, previous=current)
    if datetime.now(UTC) - row.enrolled_at > timedelta(days=30):
        return await enroll(settings, sessions, previous=current)
    return current


def _upsert_operator(session, item):
    data = item["data"]
    user = session.scalar(
        select(OperatorUser).where(OperatorUser.normalized_username == data["normalized_username"])
    )
    if user is None:
        user = OperatorUser(
            user_id=UUID(data["user_id"]),
            username=data["username"],
            normalized_username=data["normalized_username"],
            display_name=data["display_name"],
            password_hash=data["password_verifier"],
            roles=data["roles"],
        )
        session.add(user)
    else:
        user.username = data["username"]
        user.display_name = data["display_name"]
        user.password_hash = data["password_verifier"]
        user.roles = data["roles"]
        user.active = not item["tombstone"]


async def sync_once(settings, sessions):
    if not settings.site_url:
        return
    token = await credential(settings, sessions)
    with sessions() as session:
        source = session.scalar(select(Source))
    cursor = source.committed_cursor if source else 0
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(base_url=settings.site_url, timeout=30) as client:
            catalog_response = await client.get("/api/v1/signage/service/events", headers=headers)
            if catalog_response.status_code == 401 and not settings.site_credential:
                token = await enroll(settings, sessions, previous=token)
                headers = {"Authorization": f"Bearer {token}"}
                catalog_response = await client.get(
                    "/api/v1/signage/service/events", headers=headers
                )
            catalog_response.raise_for_status()
            catalog = catalog_response.json()
            site_name = installation(sessions).site_name
            for event in catalog:
                event["site_name"] = site_name
            selected = [
                x
                for x in catalog
                if not settings.event_id or x["event_id"] == str(settings.event_id)
            ]
            snapshots = []
            for event in selected:
                response = await client.get(
                    "/api/v1/signage/projection",
                    params={"cursor": cursor, "event_id": event["event_id"]},
                    headers=headers,
                )
                response.raise_for_status()
                snapshots.append((event, response.json()))
    except httpx.HTTPStatusError:
        raise
    if not snapshots:
        return
    with sessions.begin() as session:
        source = session.scalar(select(Source).with_for_update())
        first = snapshots[0][1]
        if source is None:
            source = Source(site_id=UUID(first["source_site_id"]), base_url=settings.site_url)
            session.add(source)
            session.flush()
        restored = (
            source.source_instance_id
            and str(source.source_instance_id) != first["source_instance_id"]
        )
        if restored or any(body["full_resync"] for _, body in snapshots):
            session.execute(delete(Projection).where(Projection.source_id == source.source_id))
        generation = 0
        for event, body in snapshots:
            generation = max(generation, body["snapshot_generation"])
            event_id = UUID(event["event_id"])
            event_item = {
                "type": "event",
                "id": event["event_id"],
                "event_id": event["event_id"],
                "tombstone": bool(event["archived_at"]),
                "data": event,
            }
            for item in [event_item, *body["items"]]:
                if item["type"] == "operator_user":
                    _upsert_operator(session, item)
                    continue
                entity_id = UUID(item["id"])
                row = session.scalar(
                    select(Projection).where(
                        Projection.source_id == source.source_id,
                        Projection.entity_type == item["type"],
                        Projection.entity_id == entity_id,
                    )
                )
                if row is None:
                    row = Projection(
                        source_id=source.source_id,
                        event_id=event_id,
                        entity_type=item["type"],
                        entity_id=entity_id,
                        generation=body["snapshot_generation"],
                        payload=item["data"],
                        tombstone=item["tombstone"],
                    )
                    session.add(row)
                else:
                    row.payload = item["data"]
                    row.tombstone = item["tombstone"]
                    row.generation = body["snapshot_generation"]
        source.source_instance_id = UUID(first["source_instance_id"])
        source.snapshot_generation = generation
        source.committed_cursor = max(body["committed_cursor"] for _, body in snapshots)
        source.last_sync_at = datetime.now(UTC)
        source.last_error = None


async def main():
    settings = Settings()
    sessions = factory(settings)
    while True:
        try:
            await sync_once(settings, sessions)
        except Exception as exception:
            with sessions.begin() as session:
                source = session.scalar(select(Source))
                if source:
                    source.last_error = str(exception)[:2048]
        await asyncio.sleep(settings.poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
