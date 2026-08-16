from datetime import UTC, datetime
from uuid import UUID

import pytest

from upm_shared.enums import MediaMatchState, PresentationIdentifierSource
from upm_shared.presentation_media import (
    CanonicalPresentationMetadata,
    MatchCandidate,
    allocate_presentation_identifier,
    canonical_presentation_filename,
    generate_presentation_identifier,
    match_presentation,
    operational_sort_key,
    safe_filename_component,
)


def test_imported_and_offline_generated_identifiers_are_stable_and_distinct() -> None:
    identity = UUID("018f0000-0000-7000-8000-000000abcdef")
    imported = allocate_presentation_identifier("A4-827", "STL01", identity)
    assert imported == ("A4-827", PresentationIdentifierSource.IMPORTED)
    assert generate_presentation_identifier("STL 01", identity) == "UPM-STL01-8000000000ABCDEF"
    assert generate_presentation_identifier("NYC01", identity) != generate_presentation_identifier(
        "STL01", identity
    )


@pytest.mark.parametrize("value", ["CON", "..", ' A/B:C*D?E"F<G>H|I. ', "\x00title"])
def test_filename_components_are_cross_platform_safe(value: str) -> None:
    result = safe_filename_component(value, "Untitled")
    assert result not in {"", ".", "..", "CON"}
    assert not any(character in result for character in '\\/:*?"<>|\x00')
    assert not result.endswith((" ", "."))


def test_canonical_filename_uses_event_timezone_version_and_original_extension() -> None:
    filename = canonical_presentation_filename(
        CanonicalPresentationMetadata(
            presentation_identifier="12345",
            event_timezone="America/Chicago",
            starts_at=datetime(2026, 8, 18, 14, tzinfo=UTC),
            room_label="Room 306AB",
            presenter_family_name="Smith",
            presenter_given_name="Jane",
            title="AI Infrastructure",
            version_number=2,
            original_filename="speaker.final.PPTX",
        )
    )
    assert filename == ("12345_2026-08-18_Room-306AB_0900_Smith-Jane_AI-Infrastructure_v02.pptx")


def test_canonical_filename_has_deterministic_missing_metadata_fallbacks() -> None:
    filename = canonical_presentation_filename(
        CanonicalPresentationMetadata(
            presentation_identifier="UPM-STL01-A7K92F",
            event_timezone="UTC",
            starts_at=None,
            room_label=None,
            presenter_family_name=None,
            presenter_given_name=None,
            title=None,
            version_number=1,
            original_filename="slides.pdf",
        )
    )
    assert filename == (
        "UPM-STL01-A7K92F_No-Date_Unassigned-Room_No-Time_No-Presenter_Untitled_v01.pdf"
    )


def test_canonical_filename_rejects_unsupported_or_naive_input() -> None:
    base = dict(
        presentation_identifier="123",
        event_timezone="UTC",
        starts_at=datetime(2026, 1, 1),
        room_label="R",
        presenter_family_name="S",
        presenter_given_name="J",
        title="T",
        version_number=1,
        original_filename="x.pptx",
    )
    with pytest.raises(ValueError, match="timezone"):
        canonical_presentation_filename(CanonicalPresentationMetadata(**base))
    base["starts_at"] = datetime(2026, 1, 1, tzinfo=UTC)
    base["original_filename"] = "x.exe"
    with pytest.raises(ValueError, match="unsupported"):
        canonical_presentation_filename(CanonicalPresentationMetadata(**base))


def test_matching_prefers_exact_identity_and_preserves_ambiguity() -> None:
    first = MatchCandidate(UUID(int=1), "UPM-STL01-A7K92F", "12345")
    second = MatchCandidate(UUID(int=2), "UPM-NYC01-B9Q12Z", "777")
    result = match_presentation("12345_Smith_slides.pptx", [first, second])
    assert result.state is MediaMatchState.EXACT
    assert result.presentation_id == first.presentation_id
    ambiguous = match_presentation("12345_777_deck.pdf", [first, second])
    assert ambiguous.state is MediaMatchState.AMBIGUOUS
    assert ambiguous.presentation_id is None
    assert match_presentation("Smith.pptx", [first]).state is MediaMatchState.UNMATCHED


def test_operational_sorting_is_date_room_time_presenter_title_version() -> None:
    values = [
        (datetime(2026, 8, 18, 15, tzinfo=UTC), "B", "Smith", "Jane", "Z", 1),
        (datetime(2026, 8, 18, 14, tzinfo=UTC), "A", "Smith", "Jane", "A", 2),
        (datetime(2026, 8, 18, 14, tzinfo=UTC), "A", "Brown", "Sam", "Z", 1),
    ]
    ordered = sorted(
        values,
        key=lambda row: operational_sort_key(
            row[0], "America/Chicago", row[1], row[2], row[3], row[4], row[5]
        ),
    )
    assert [row[2] for row in ordered] == ["Brown", "Smith", "Smith"]
