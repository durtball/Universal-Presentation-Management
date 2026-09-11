import asyncio
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy import delete, select

from .config import Settings
from .db import factory
from .models import Projection, Source


async def sync_once(settings, sessions):
    if not settings.site_url or not settings.site_credential or not settings.event_id:
        return
    with sessions() as session:
        source = session.scalar(select(Source))
    cursor = source.committed_cursor if source else 0
    async with httpx.AsyncClient(base_url=settings.site_url, timeout=30) as client:
        response = await client.get(
            "/api/v1/signage/projection",
            params={"cursor": cursor, "event_id": str(settings.event_id)},
            headers={"Authorization": f"Bearer {settings.site_credential}"},
        )
        response.raise_for_status()
        body = response.json()
    with sessions.begin() as session:
        source = session.scalar(select(Source).with_for_update())
        if source is None:
            source = Source(
                site_id=UUID(body["source_site_id"]),
                base_url=settings.site_url,
                credential=settings.site_credential,
            )
            session.add(source)
            session.flush()
        restored = (
            source.source_instance_id
            and str(source.source_instance_id) != body["source_instance_id"]
        )
        if body["full_resync"] or restored:
            session.execute(delete(Projection).where(Projection.source_id == source.source_id))
        for item in body["items"]:
            row = session.scalar(
                select(Projection).where(
                    Projection.source_id == source.source_id,
                    Projection.entity_type == item["type"],
                    Projection.entity_id == UUID(item["id"]),
                )
            )
            if row is None:
                row = Projection(
                    source_id=source.source_id,
                    event_id=UUID(item["event_id"]),
                    entity_type=item["type"],
                    entity_id=UUID(item["id"]),
                    generation=body["snapshot_generation"],
                    payload=item["data"],
                    tombstone=item["tombstone"],
                )
                session.add(row)
            else:
                row.payload = item["data"]
                row.tombstone = item["tombstone"]
                row.generation = body["snapshot_generation"]
        source.source_instance_id = UUID(body["source_instance_id"])
        source.snapshot_generation = body["snapshot_generation"]
        source.committed_cursor = body["committed_cursor"]
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
