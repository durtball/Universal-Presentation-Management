from pathlib import Path
from uuid import UUID

from upm_site.persistence.models import MediaTransferSession


def test_partial_transfer_reference_is_opaque_and_durable() -> None:
    target_id = UUID("018f0000-0000-7000-8000-000000000001")
    transfer = MediaTransferSession(
        transfer_session_id=UUID("018f0000-0000-7000-8000-000000000002"),
        site_id=UUID("018f0000-0000-7000-8000-000000000003"),
        event_id=UUID("018f0000-0000-7000-8000-000000000004"),
        presentation_id=UUID("018f0000-0000-7000-8000-000000000005"),
        presentation_version_id=UUID("018f0000-0000-7000-8000-000000000006"),
        original_filename="deck.pptx",
        canonical_filename="deck.pptx",
        expected_size=1,
        sha256="0" * 64,
        partial_key="staging/opaque.upload",
        storage_target_id=target_id,
    )
    assert transfer.storage_target_id == target_id
    assert transfer.partial_key == "staging/opaque.upload"


def test_site_runtime_does_not_mount_legacy_presentation_data_path() -> None:
    repository = Path(__file__).resolve().parents[2]
    compose = (repository / "docker-compose.site.yml").read_text()
    config = (repository / "site/python/src/upm_site/config.py").read_text()

    assert "${SITE_MEDIA_HOST_PATH" not in compose
    assert "/data/objects" not in compose
    assert "/data/staging" not in compose
    assert "media_mount_path" not in config
    assert "staging_mount_path" not in config
