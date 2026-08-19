"""Focused managed SMB presentation path and filesystem reconciliation coverage."""

import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from upm_media_storage.api import create_app
from upm_media_storage.config import Settings
from upm_shared.smb_materialization import (
    MaterializationItem,
    paths_for,
    safe_component,
    with_collision_suffixes,
)


def item(**overrides):
    values = {
        "presentation_id": UUID("0198c000-0000-7000-8000-000000000001"),
        "version_id": UUID("0198c000-0000-7000-8000-000000000002"),
        "event_id": UUID("0198c000-0000-7000-8000-000000000003"),
        "event_name": "UPM: Event?",
        "event_timezone": "America/Chicago",
        "presentation_identifier": "3261639",
        "title": "The Cloud Playbook",
        "presenters": ("Lovelace",),
        "original_filename": "Cloud Playbook Final v3.pptx",
        "storage_target_id": UUID("0198c000-0000-7000-8000-000000000004"),
        "storage_key": "objects/deck",
        "sha256": "a" * 64,
        "session_id": UUID("0198c000-0000-7000-8000-000000000005"),
        "session_external_id": "SESSION/42",
        "session_title": "Future: Now",
        "starts_at": datetime(2026, 8, 19, 14, 30, tzinfo=UTC),
        "room_name": "Ballroom | A",
    }
    values.update(overrides)
    return MaterializationItem(**values)


def test_paths_are_windows_safe_event_local_and_human_readable():
    paths = paths_for(item())
    assert len(paths) == 4
    assert any("By Schedule/UPM_ Event_/2026-08-19/09-30 AM/Ballroom _ A" in p for p in paths)
    assert any(p.startswith("By Room/") for p in paths)
    assert any(p.startswith("By Presenter/") for p in paths)
    assert any(p.startswith("All Presentations/") for p in paths)
    assert all(p.endswith("3261639 - Lovelace - Cloud Playbook Final v3.pptx") for p in paths)
    assert all("0198c000" not in p for p in paths)
    assert safe_component("CON. ", item().presentation_id) == "_CON"


def test_presenters_fallbacks_unicode_and_duplicate_extension():
    paths = paths_for(
        item(presenters=("Forsythe", "O’Neill"), original_filename="Résumé (Final).PPTX")
    )
    assert all("Forsythe-O’Neill - Résumé (Final).PPTX" in path for path in paths)
    assert not any(".PPTX.PPTX" in path for path in paths)
    unknown = paths_for(item(presenters=(), original_filename=None, title="Canonical Title"))
    assert all("Unknown Presenter - Canonical Title" in path for path in unknown)


def test_reserved_invalid_and_long_original_names_preserve_identity_presenter_extension():
    invalid = paths_for(item(original_filename="CON: plan / final?.pptx"))
    assert all(": plan" not in path and "/ final?" not in path for path in invalid)
    long_paths = paths_for(
        item(
            event_name="E" * 80,
            session_title="S" * 80,
            original_filename=f"{'Recognizable ' * 80}.pptx",
        )
    )
    assert all(len(path) <= 240 for path in long_paths)
    assert all("3261639 - Lovelace - " in path and path.endswith(".pptx") for path in long_paths)


def test_collision_suffix_is_added_only_for_a_genuine_collision():
    first = item()
    second = item(
        presentation_id=UUID("0198c000-0000-7000-8000-000000000099"),
        version_id=UUID("0198c000-0000-7000-8000-000000000098"),
    )
    single = with_collision_suffixes([first])
    assert single[0].collision_suffix is None
    collided = with_collision_suffixes([second, first])
    assert sum(value.collision_suffix is not None for value in collided) == 1
    assert len({paths_for(value)[0] for value in collided}) == 2


def test_storage_reconciliation_hardlinks_updates_and_removes_stale_paths(tmp_path):
    media = tmp_path / "media"
    share = tmp_path / "presentations"
    media.mkdir()
    source = media / "objects" / "deck"
    source.parent.mkdir()
    source.write_bytes(b"canonical bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target_id = item().storage_target_id
    settings = Settings(
        service_token="test-token",
        state_path=tmp_path / "state.json",
        smb_presentations_path=share,
        targets_json=json.dumps(
            [
                {
                    "storage_target_id": str(target_id),
                    "name": "media",
                    "internal_path": str(media),
                    "roles": ["media", "staging"],
                }
            ]
        ),
    )
    client = TestClient(create_app(settings))
    headers = {"Authorization": "Bearer test-token"}
    entry = {
        "relative_path": "All Presentations/Event/Deck.pptx",
        "storage_target_id": str(target_id),
        "storage_key": "objects/deck",
        "sha256": digest,
    }
    first = client.put(
        "/api/v1/storage/smb/presentations", headers=headers, json={"entries": [entry]}
    )
    assert first.status_code == 200
    visible = share / entry["relative_path"]
    assert visible.read_bytes() == b"canonical bytes"
    assert os.stat(source).st_ino == os.stat(visible).st_ino
    assert (
        client.put(
            "/api/v1/storage/smb/presentations", headers=headers, json={"entries": [entry]}
        ).json()["added"]
        == 0
    )
    assert (
        client.put(
            "/api/v1/storage/smb/presentations", headers=headers, json={"entries": []}
        ).json()["removed"]
        == 1
    )
    assert source.read_bytes() == b"canonical bytes"
