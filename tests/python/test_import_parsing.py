"""Fast import parsing and header-detection regressions."""

import pytest
from fastapi import HTTPException

from upm_central.imports import _normalized, _parse_xlsx, _presenter_emails, detect_columns


def test_invalid_xlsx_is_an_operator_visible_validation_error() -> None:
    with pytest.raises(HTTPException) as caught:
        _parse_xlsx(b"not an xlsx archive")
    assert caught.value.status_code == 422
    assert caught.value.detail == "invalid XLSX workbook"


def test_wide_program_headers_map_to_existing_domain_fields() -> None:
    values = _normalized(
        {
            "Speaker Name": "Alex Example",
            "Speaker Email": "alex@example.test",
            "Company": "Example Org",
            "Session Title": "Session",
            "Presentation Title": "Presentation",
            "Room": "Grand Ballroom",
        }
    )
    assert values["display_name"] == "Alex Example"
    assert values["email"] == "alex@example.test"
    assert values["organization"] == "Example Org"
    assert values["location_name"] == "Grand Ballroom"
    assert detect_columns(["Speaker Name", "Room"]) == {
        "Speaker Name": "display_name",
        "Room": "location_name",
    }


def test_identifier_and_expected_filename_headers_are_normalized() -> None:
    values = _normalized(
        {
            "Session ID": "session-42",
            "Presenter ID": "speaker-9",
            "Presentation ID": "deck-7",
            "Presentation File": "Opening Keynote.pptx",
        }
    )
    assert values == {
        "session_external_id": "session-42",
        "session_code": "session-42",
        "presenter_external_id": "speaker-9",
        "external_id": "speaker-9",
        "external_namespace": "presenter",
        "presentation_external_id": "deck-7",
        "presentation_code": "deck-7",
        "presentation_filename": "Opening Keynote.pptx",
    }


def test_delimited_presenter_emails_are_normalized_and_invalid_values_ignored() -> None:
    assert _presenter_emails(
        {"presenter_emails": " first@example.test;invalid, SECOND@example.test "}
    ) == ["first@example.test", "second@example.test"]
