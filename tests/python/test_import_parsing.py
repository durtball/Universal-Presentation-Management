"""Fast import parsing and header-detection regressions."""

import io

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from upm_central.imports import _normalized, _parse_xlsx, detect_columns


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


PRODUCTION_HEADERS = [
    "Presentation Date Added",
    "Presentation ID",
    "Presentation Title",
    "Date",
    "Start Time",
    "End Time",
    "Track",
    "Session Format",
    "Room",
    "Presenter Roster Order",
    "Role",
    "PresenterID",
    "First Name",
    "Last Name",
    "Position / Title",
    "Organization",
    "Email",
]


def _production_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Program Roster"
    sheet.append(PRODUCTION_HEADERS + [None] * 40)
    for index in range(32):
        program = "3468947" if index < 3 else "P-2" if index < 5 else f"P-{index}"
        presenter = "PERSON-1" if index in {0, 4} else f"PERSON-{index + 1}"
        sheet.append(
            [
                "2027-01-01",
                program,
                "Agents Are Not Tasks" if index < 3 else f"Program {program}",
                "2027-04-10" if index % 2 == 0 else "2027-04-11",
                "09:00:00",
                "10:00:00",
                f"Track {index % 3}",
                "Panel" if index % 2 else "Lecture",
                "Venetian Ballroom F" if index % 2 else "Palazzo Ballroom G",
                (index % 3) + 1,
                "Session Sponsor" if index == 0 else "Sponsor Presenter",
                presenter,
                f"Given{index}",
                f"Family{index}",
                None if index == 2 else "Engineer",
                "Example Org",
                f"person{1 if index in {0, 4} else index + 1}@example.test",
            ]
            + [None] * 40
        )
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_production_workbook_preserves_every_populated_row_and_ignores_trailing_columns() -> None:
    rows = _parse_xlsx(_production_workbook())
    assert len(rows) == 32
    assert rows[0]["__worksheet"] == "Program Roster"
    assert len([key for key in rows[0] if not key.startswith("__")]) == 17
    mapped = detect_columns(list(rows[0]))
    assert len(mapped) == 17
    assert mapped["Presentation ID"] == "session_code"
    assert mapped["PresenterID"] == "external_id"
    assert mapped["Presentation Date Added"] == "presentation_date_added"
    assert mapped["Position / Title"] == "professional_title"

    normalized = [_normalized(row) for row in rows]
    assert sum(row["session_code"] == "3468947" for row in normalized) == 3
    assert sum(row["external_id"] == "PERSON-1" for row in normalized) == 2
    assert normalized[2]["professional_title"] is None
    assert normalized[0]["starts_at"] == "2027-04-10T09:00:00"
    assert normalized[0]["ends_at"] == "2027-04-10T10:00:00"


def test_alias_vocabulary_keeps_specific_titles_dates_and_identity_distinct() -> None:
    mapping = detect_columns(
        [
            "Presentation_ID",
            "Speaker ID",
            "Session Name",
            "Event Date",
            "Title / Position",
            "Venue Room",
            "Speaker Order",
            "Presentation Type",
        ]
    )
    assert mapping == {
        "Presentation_ID": "session_code",
        "Speaker ID": "external_id",
        "Session Name": "session_title",
        "Event Date": "session_date",
        "Title / Position": "professional_title",
        "Venue Room": "location_name",
        "Speaker Order": "presenter_order",
        "Presentation Type": "session_format",
    }
