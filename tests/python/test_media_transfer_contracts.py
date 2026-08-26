from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from upm_shared.contracts.media_transfer import (
    MediaTransferFinalizeResult,
    MediaTransferManifest,
    MediaTransferProgress,
    transfer_idempotency_key,
)
from upm_shared.enums import MediaReplicationState, MediaTransferState, SourceSystem

TRANSFER_ID = UUID("018f0000-0000-7000-8000-000000000001")
SITE_ID = UUID("018f0000-0000-7000-8000-000000000002")
EVENT_ID = UUID("018f0000-0000-7000-8000-000000000003")
PRESENTATION_ID = UUID("018f0000-0000-7000-8000-000000000004")
VERSION_ID = UUID("018f0000-0000-7000-8000-000000000005")
MEDIA_ID = UUID("018f0000-0000-7000-8000-000000000006")
SHA256 = "a" * 64


def manifest(**overrides) -> MediaTransferManifest:
    values = {
        "transfer_session_id": TRANSFER_ID,
        "origin_system": SourceSystem.CENTRAL,
        "destination_site_id": SITE_ID,
        "event_id": EVENT_ID,
        "presentation_id": PRESENTATION_ID,
        "presentation_version_id": VERSION_ID,
        "presentation_identifier": "12345",
        "original_filename": "speaker upload.pptx",
        "canonical_filename": "12345_2026-08-18_Room_0900_Smith-Jane_Title_v01.pptx",
        "expected_size": 100,
        "sha256": SHA256,
        "created_at": datetime(2026, 8, 16, tzinfo=UTC),
    }
    values.update(overrides)
    return MediaTransferManifest.model_validate(values)


def test_manifest_is_site_specific_and_round_trips() -> None:
    item = manifest()
    assert item.destination_site_id == SITE_ID
    assert MediaTransferManifest.model_validate_json(item.model_dump_json()) == item
    assert transfer_idempotency_key(TRANSFER_ID, SITE_ID) == (
        f"media-transfer:{SITE_ID}:{TRANSFER_ID}"
    )


@pytest.mark.parametrize("extension", ["jpg", "ppsx"])
def test_jpg_and_ppsx_transfer_manifests_preserve_original_extension(extension: str) -> None:
    item = manifest(
        original_filename=f"session-media.{extension}",
        canonical_filename=f"P-100_session-media_v01.{extension}",
    )
    restored = MediaTransferManifest.model_validate_json(item.model_dump_json())
    assert restored.original_filename.endswith(f".{extension}")
    assert restored.canonical_filename.endswith(f".{extension}")


def test_direction_requires_the_corresponding_site_identity() -> None:
    with pytest.raises(ValidationError, match="destination_site_id"):
        manifest(destination_site_id=None)
    with pytest.raises(ValidationError, match="origin_site_id"):
        manifest(origin_system=SourceSystem.SITE, destination_site_id=None)


@pytest.mark.parametrize("offset", [-1, 101])
def test_progress_offset_must_be_within_expected_size(offset: int) -> None:
    with pytest.raises(ValidationError):
        MediaTransferProgress(
            transfer_session_id=TRANSFER_ID,
            site_id=SITE_ID,
            expected_size=100,
            confirmed_offset=offset,
            state=MediaTransferState.TRANSFERRING,
            last_progress_at=datetime(2026, 8, 16, tzinfo=UTC),
        )


def test_completed_progress_requires_the_full_range() -> None:
    with pytest.raises(ValidationError, match="full expected byte range"):
        MediaTransferProgress(
            transfer_session_id=TRANSFER_ID,
            site_id=SITE_ID,
            expected_size=100,
            confirmed_offset=99,
            state=MediaTransferState.COMPLETED,
            last_progress_at=datetime(2026, 8, 16, tzinfo=UTC),
        )


def test_sha256_is_lowercase_hex_and_completion_requires_media() -> None:
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        manifest(sha256="not-a-sha")
    with pytest.raises(ValidationError, match="media_object_id"):
        MediaTransferFinalizeResult(
            transfer_session_id=TRANSFER_ID,
            site_id=SITE_ID,
            expected_size=100,
            confirmed_offset=100,
            sha256=SHA256,
            state=MediaTransferState.COMPLETED,
            replication_state=MediaReplicationState.SYNCED,
        )
    completed = MediaTransferFinalizeResult(
        transfer_session_id=TRANSFER_ID,
        site_id=SITE_ID,
        expected_size=100,
        confirmed_offset=100,
        sha256=SHA256,
        state=MediaTransferState.COMPLETED,
        media_object_id=MEDIA_ID,
        replication_state=MediaReplicationState.SYNCED,
    )
    assert completed.media_object_id == MEDIA_ID
