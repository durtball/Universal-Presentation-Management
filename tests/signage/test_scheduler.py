from datetime import UTC, datetime

from upm_signage.scheduler import effective_override, room_door


def test_offline_scheduler_advances_and_ignores_cancelled_sessions():
    rows = [
        {
            "session_id": "one",
            "room_id": "room",
            "title": "Opening",
            "starts_at": "2026-09-10T10:00:00+00:00",
            "ends_at": "2026-09-10T11:00:00+00:00",
            "cancelled": False,
        },
        {
            "session_id": "cancelled",
            "room_id": "room",
            "title": "Cancelled",
            "starts_at": "2026-09-10T11:00:00+00:00",
            "ends_at": "2026-09-10T12:00:00+00:00",
            "cancelled": True,
        },
        {
            "session_id": "two",
            "room_id": "room",
            "title": "Closing",
            "starts_at": "2026-09-10T12:00:00+00:00",
            "ends_at": "2026-09-10T13:00:00+00:00",
            "cancelled": False,
        },
    ]
    first = room_door(rows, datetime(2026, 9, 10, 10, 30, tzinfo=UTC), "room")
    later = room_door(rows, datetime(2026, 9, 10, 12, 30, tzinfo=UTC), "room")
    assert first["current"]["session_id"] == "one" and first["next"]["session_id"] == "two"
    assert later["current"]["session_id"] == "two"


def test_override_expiry_is_deterministic():
    rows = [
        {
            "override_id": "a",
            "starts_at": "2026-09-10T10:00:00+00:00",
            "expires_at": "2026-09-10T11:00:00+00:00",
        }
    ]
    assert effective_override(rows, datetime(2026, 9, 10, 10, 59, tzinfo=UTC))["override_id"] == "a"
    assert effective_override(rows, datetime(2026, 9, 10, 11, 0, tzinfo=UTC)) is None


def test_player_bundles_persistent_offline_shell():
    from pathlib import Path

    player = Path("signage/player/index.html").read_text()
    worker = Path("signage/player/sw.js").read_text()
    assert "indexedDB.open" in player
    assert "serviceWorker.register" in player
    assert "OFFLINE · CACHED SCHEDULE" in player
    assert "cache.addAll" in worker
    assert "URLSearchParams(location.search)" not in player
    assert "localStorage.token" not in player
    assert "upmSetPlayerCredential" in player


def test_layout_editor_matches_required_designer_surfaces():
    from pathlib import Path

    editor = Path("signage/web/index.html").read_text()
    for label in (
        "Create Layout",
        "Add Element",
        "Current Session Block",
        "Canvas Size",
        "Template Breakpoints",
        "Save Draft",
        "Publish Layout",
        "Position & Size",
        "Typography",
    ):
        assert label in editor
    assert "/api/v1/layouts" in editor
    assert "localStorage.operatorPassword" not in editor
    assert "/auth/login" in editor
    assert "upmSetOperatorPassword" not in editor
    assert "Element editing is unavailable" not in editor


def test_native_signage_is_independent_and_does_not_expose_credentials():
    from pathlib import Path

    project = Path("clients/windows/UPM.Signage/UPM.Signage.csproj").read_text()
    source = "\n".join(
        path.read_text() for path in Path("clients/windows/UPM.Signage").glob("*.cs")
    )
    assert "<AssemblyName>UPM.Signage</AssemblyName>" in project
    assert "RoomAgent" not in project
    assert "PasswordVault" in source
    assert "PlayerCredential" not in source or "PairingResult" in source
    assert "?token=" not in source
    assert "last-verified-manifest.json" in source
    assert "rendererRecoveries > 3" in source
