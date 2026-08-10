"""Shared contract and domain-rule tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from upm_shared.contracts.base import OwnershipMetadata, SyncMetadata
from upm_shared.contracts.entities import (
    EventParticipationContract,
    MediaObjectContract,
    PersonContract,
    PresentationAssetContract,
    StorageTargetContract,
)
from upm_shared.domain.media import LogicalMediaLocation, validate_object_key
from upm_shared.enums import (
    AssetKind,
    MediaAvailability,
    MediaCategory,
    SourceSystem,
    StorageType,
)
from upm_shared.identifiers import new_uuid7


def sync_metadata() -> SyncMetadata:
    now = datetime.now(UTC)
    return SyncMetadata(created_at=now, updated_at=now)


def test_person_identity_is_separate_from_event_participation() -> None:
    person_id = new_uuid7()
    event_id = new_uuid7()
    person = PersonContract(
        person_id=person_id,
        display_name="Jane Doe",
        metadata=sync_metadata(),
    )
    participation = EventParticipationContract(
        event_participation_id=new_uuid7(),
        event_id=event_id,
        person_id=person_id,
        metadata=sync_metadata(),
    )

    assert participation.person_id == person.person_id
    assert participation.event_participation_id != person.person_id


def test_storage_target_accepts_configurable_absolute_linux_path() -> None:
    contract = StorageTargetContract(
        storage_target_id=new_uuid7(),
        site_id=new_uuid7(),
        display_name="Primary media",
        storage_type=StorageType.LOCAL_FILESYSTEM,
        root_path="/mnt/upm-media/",
        warning_free_bytes=100,
        critical_free_bytes=50,
        metadata=sync_metadata(),
    )

    assert contract.root_path == "/mnt/upm-media"


@pytest.mark.parametrize(
    ("root_path", "warning", "critical"),
    [("relative/path", 100, 50), ("/mnt/media", 49, 50)],
)
def test_storage_target_rejects_invalid_configuration(
    root_path: str, warning: int, critical: int
) -> None:
    with pytest.raises(ValidationError):
        StorageTargetContract(
            storage_target_id=new_uuid7(),
            site_id=new_uuid7(),
            display_name="Invalid",
            storage_type=StorageType.LOCAL_FILESYSTEM,
            root_path=root_path,
            warning_free_bytes=warning,
            critical_free_bytes=critical,
            metadata=sync_metadata(),
        )


@pytest.mark.parametrize("object_key", ["/absolute/file.pptx", "../escape", "a\\b.pptx"])
def test_media_object_key_rejects_host_paths_and_traversal(object_key: str) -> None:
    with pytest.raises(ValueError):
        validate_object_key(object_key)


def test_media_location_uses_storage_target_and_logical_key() -> None:
    target_id = new_uuid7()
    location = LogicalMediaLocation(target_id, "events/event-1/presentation.pptx")
    media = MediaObjectContract(
        media_object_id=new_uuid7(),
        storage_target_id=target_id,
        object_key=location.object_key,
        category=MediaCategory.PRESENTATION,
        original_filename="presentation.pptx",
        size_bytes=1,
        content_hash="a" * 64,
        hash_algorithm="sha256",
        availability=MediaAvailability.AVAILABLE,
        ownership=OwnershipMetadata(owning_site_id=new_uuid7(), source_system=SourceSystem.SITE),
        metadata=sync_metadata(),
    )

    assert media.storage_target_id == location.storage_target_id
    assert media.object_key == location.object_key


def test_derivative_asset_requires_source_asset() -> None:
    with pytest.raises(ValidationError):
        PresentationAssetContract(
            presentation_asset_id=new_uuid7(),
            presentation_version_id=new_uuid7(),
            media_object_id=new_uuid7(),
            kind=AssetKind.DERIVATIVE,
            metadata=sync_metadata(),
        )
