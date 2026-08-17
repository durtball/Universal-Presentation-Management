from uuid import uuid4

import httpx
import pytest

from upm_central.media_replication import finalize_replication, safe_replication_path
from upm_central.persistence.models import MediaReplicationReceiveSession
from upm_shared.enums import MediaReplicationState, MediaTransferState, StorageType
from upm_site.config import SiteSettings
from upm_site.media.replication import execute_central_push
from upm_site.persistence.models import (
    CentralRegistration,
    MediaObject,
    MediaReplicationSession,
    Presentation,
    StorageTarget,
    TransferJob,
)


class FakeSession:
    def __init__(self, objects):
        self.objects = objects
        self.added = []

    def get(self, model, identity, **_kwargs):
        return self.objects.get((model, identity))

    def add(self, value):
        self.added.append(value)

    def flush(self):
        pass


def test_site_push_resumes_from_central_confirmed_offset(tmp_path, monkeypatch):
    data = b"0123456789abcdef"
    site_id, event_id, presentation_id, version_id, media_id, replication_id = [
        uuid4() for _ in range(6)
    ]
    target = StorageTarget(
        storage_target_id=uuid4(),
        site_id=site_id,
        display_name="media",
        storage_type=StorageType.LOCAL_FILESYSTEM,
        root_path=str(tmp_path),
        enabled=True,
        primary_media=True,
    )
    media = MediaObject(
        media_object_id=media_id,
        site_id=site_id,
        event_id=event_id,
        storage_target_id=target.storage_target_id,
        object_key="presentation-versions/file",
        category="presentation_version",
        original_filename="deck.pptx",
    )
    source = tmp_path / media.object_key
    source.parent.mkdir()
    source.write_bytes(data)
    replication = MediaReplicationSession(
        replication_session_id=replication_id,
        site_id=site_id,
        event_id=event_id,
        presentation_id=presentation_id,
        presentation_version_id=version_id,
        media_object_id=media_id,
        expected_size=len(data),
        sha256="0" * 64,
        original_filename="deck.pptx",
        confirmed_offset=0,
    )
    presentation = Presentation(
        presentation_id=presentation_id,
        event_id=event_id,
        title="Deck",
        presentation_identifier="UPM-TEST-ABC123",
    )
    registration = CentralRegistration(site_id=site_id, central_url="https://central")
    job = TransferJob(
        transfer_job_id=replication_id,
        site_id=site_id,
        transfer_type="presentation_media.central_push",
        payload={},
    )
    session = FakeSession(
        {
            (MediaReplicationSession, replication_id): replication,
            (MediaObject, media_id): media,
            (StorageTarget, target.storage_target_id): target,
            (Presentation, presentation_id): presentation,
            (CentralRegistration, site_id): registration,
        }
    )
    monkeypatch.setattr("upm_site.media.replication.decrypt_secret", lambda *_: "secret")
    received = bytearray(data[:5])
    offsets = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/media-replications"):
            return httpx.Response(200, json={"confirmed_offset": 5})
        if request.method == "PUT":
            offset = int(request.url.params["offset"])
            offsets.append(offset)
            received.extend(request.content)
            return httpx.Response(200, json={"confirmed_offset": len(received)})
        return httpx.Response(
            200,
            json={
                "replication_state": "synced",
                "presentation_version_id": str(version_id),
                "central_media_object_id": str(media_id),
            },
        )

    settings = SiteSettings(
        database_url="postgresql+psycopg://u:p@db/site",
        credential_encryption_key="x" * 32,
        media_mount_path=str(tmp_path),
        transfer_block_bytes=65_536,
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))

    class Storage:
        def read_object(self, _target_id, _key, offset, limit):
            return data[offset : offset + limit]

    assert execute_central_push(session, None, settings, job, client, Storage())
    assert offsets == [5]
    assert bytes(received) == data
    assert replication.state is MediaReplicationState.SYNCED


def test_central_finalization_verifies_and_is_idempotent(tmp_path, monkeypatch):
    data = b"replicated presentation"
    import hashlib

    ids = [uuid4() for _ in range(6)]
    session_id, site_id, event_id, presentation_id, version_id, media_id = ids
    receiver = MediaReplicationReceiveSession(
        replication_session_id=session_id,
        origin_site_id=site_id,
        event_id=event_id,
        presentation_id=presentation_id,
        presentation_version_id=version_id,
        source_media_object_id=media_id,
        presentation_identifier="UPM-X-1",
        original_filename="deck.pdf",
        expected_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        partial_key=str(session_id),
        confirmed_offset=len(data),
        state=MediaTransferState.TRANSFERRING,
        replication_state=MediaReplicationState.SYNCING,
    )
    safe_replication_path(tmp_path, str(session_id)).write_bytes(data)
    session = FakeSession({})
    monkeypatch.setattr(
        "upm_central.media_replication.authorize_replication_context", lambda *a, **k: object()
    )
    replica = finalize_replication(session, tmp_path, receiver)
    session.objects[(type(replica), media_id)] = replica
    assert replica.media_object_id == media_id
    assert receiver.presentation_version_id == version_id
    assert receiver.replication_state is MediaReplicationState.SYNCED
    assert finalize_replication(session, tmp_path, receiver) is replica
    assert len([item for item in session.added if type(item) is type(replica)]) == 1


def test_replication_partial_key_rejects_paths(tmp_path):
    with pytest.raises(ValueError):
        safe_replication_path(tmp_path, "../escape")
