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
    normalize_source_relative_path,
    operational_sort_key,
    safe_filename_component,
)


def test_source_relative_path_is_safe_provenance_only() -> None:
    assert (
        normalize_source_relative_path("Event Slides/Monday/Room 101/1001.pptx", "1001.pptx")
        == "Event Slides/Monday/Room 101/1001.pptx"
    )
    for unsafe in (
        "../deck.pptx",
        "/deck.pptx",
        r"C:\deck.pptx",
        r"\\server\deck.pptx",
        "root/../deck.pptx",
        "root/deck.pptx\x00",
    ):
        with pytest.raises(ValueError):
            normalize_source_relative_path(unsafe, "deck.pptx")


def test_matcher_prefers_longest_known_identifier_prefix_and_boundaries() -> None:
    ids = ["3261639", "3273219", "3418952", "A4-827", "123", "1234"]
    candidates = [
        MatchCandidate(UUID(int=index + 1), f"UPM-{value}", value)
        for index, value in enumerate(ids)
    ]
    examples = {
        "3261639-Forsythe.pptx": "3261639",
        "3273219_Seiberling.pptx": "3273219",
        "3418952 Kaneta.pptx": "3418952",
        "A4-827_Smith.pptx": "A4-827",
        "1234-Jones.pptx": "1234",
    }
    for filename, expected in examples.items():
        result = match_presentation(filename, candidates)
        assert result.state is MediaMatchState.SUGGESTED
        assert result.presentation_id == candidates[ids.index(expected)].presentation_id
        assert expected in result.reason


def test_no_match_is_reviewable_not_an_exception() -> None:
    result = match_presentation(
        "random-speaker-deck.pptx",
        [MatchCandidate(UUID(int=1), "UPM-123", "123")],
    )
    assert result.state is MediaMatchState.UNMATCHED
    assert result.presentation_id is None


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
    assert result.state is MediaMatchState.SUGGESTED
    assert result.presentation_id == first.presentation_id
    duplicate = MatchCandidate(UUID(int=3), "UPM-OTHER", "12345")
    ambiguous = match_presentation("12345_deck.pdf", [first, duplicate])
    assert ambiguous.state is MediaMatchState.AMBIGUOUS
    assert ambiguous.presentation_id is None
    assert match_presentation("Smith.pptx", [first]).state is MediaMatchState.UNMATCHED


def test_identifier_and_surname_produce_explained_high_suggestion() -> None:
    lomow = MatchCandidate(
        UUID(int=1),
        "3261629",
        "3261629",
        title="AI Infrastructure",
        presenter_family_name="Lomow",
        presenter_given_name="Steven",
        session_title="Infrastructure",
        room="306AB",
    )
    other = MatchCandidate(UUID(int=2), "999", "999", presenter_family_name="Smith")
    result = match_presentation("3261629-Lomow.pptx", [other, lomow])
    assert result.state is MediaMatchState.SUGGESTED
    assert result.presentation_id == lomow.presentation_id
    assert result.confidence == "high"
    assert "Presentation ID" in result.reason
    assert "last name" in result.reason


def test_session_code_and_samantha_lomow_produce_high_suggestion() -> None:
    candidate = MatchCandidate(
        UUID(int=1),
        "UPM-CENTRAL-ABCDEF",
        title="Reinventing Legacy Brands - Breaking the Rules Without Breaking the Brand",
        presenter_family_name="Lomow",
        presenter_given_name="Samantha",
        session_title=("Reinventing Legacy Brands - Breaking the Rules Without Breaking the Brand"),
        session_external_id="3261629",
        room="Marcello Ballroom 4403",
    )
    result = match_presentation("3261629-Lomow.pptx", [candidate])
    assert result.state is MediaMatchState.SUGGESTED
    assert result.presentation_id == candidate.presentation_id
    assert result.confidence == "high"
    assert "Session ID 3261629 matched filename" in result.reason
    assert "Presenter last name Lomow matched filename" in result.reason


def test_unique_surname_suggests_but_ambiguous_surname_does_not() -> None:
    unique = MatchCandidate(UUID(int=1), "101", presenter_family_name="Lomow")
    assert match_presentation("Lomow.pptx", [unique]).state is MediaMatchState.SUGGESTED
    duplicate = MatchCandidate(UUID(int=2), "102", presenter_family_name="Lomow")
    result = match_presentation("Lomow.pptx", [unique, duplicate])
    assert result.state is MediaMatchState.AMBIGUOUS
    assert result.presentation_id is None


def test_conflicting_strong_identifier_and_surname_requires_review() -> None:
    identifier = MatchCandidate(UUID(int=1), "3261629", presenter_family_name="Smith")
    surname = MatchCandidate(UUID(int=2), "999", presenter_family_name="Lomow")
    result = match_presentation("3261629-Lomow.pptx", [identifier, surname])
    assert result.state is MediaMatchState.AMBIGUOUS
    assert result.has_conflict is True
    assert result.presentation_id is None


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
