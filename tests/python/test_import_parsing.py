"""Fast import parsing and header-detection regressions."""

import io
from datetime import datetime, time

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from upm_central.imports import (
    _normalized,
    _parse_xlsx,
    _prepare_row,
    _wide_row_session,
    detect_columns,
)
from upm_central.persistence.models import Event


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


def test_wide_program_session_composite_is_stable_and_discriminating() -> None:
    shared = {
        "Date": "2027-06-01",
        "Start Time": "09:00",
        "End Time": "10:00",
        "Room": "Venetian Ballroom F",
        "Track": "Cloud",
        "Session Format": "Panel",
        "Presentation Title": "First talk",
    }
    first = _wide_row_session(_normalized(shared))
    another_presentation = _wide_row_session(
        _normalized({**shared, "Presentation Title": "Second talk"})
    )
    later = _wide_row_session(_normalized({**shared, "Start Time": "11:00"}))
    next_room = _wide_row_session(_normalized({**shared, "Room": "Venetian Ballroom G"}))
    next_day = _wide_row_session(_normalized({**shared, "Date": "2027-06-02"}))

    assert first["session_code"] == another_presentation["session_code"]
    assert (
        len(
            {
                first["session_code"],
                later["session_code"],
                next_room["session_code"],
                next_day["session_code"],
            }
        )
        == 4
    )
    assert first["session_title"] == "Cloud — 09:00 — Venetian Ballroom F"


def test_explicit_source_session_identifier_takes_precedence() -> None:
    values = _wide_row_session(
        _normalized(
            {
                "Session ID": "SESSION-42",
                "Presentation ID": "P-42",
                "Presentation Title": "Explicit identity",
            }
        )
    )
    assert values["session_code"] == "SESSION-42"
    assert values["presentation_code"] == "P-42"


def test_native_xlsx_date_and_time_cells_reach_session_grouping_pipeline() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Presentation ID",
            "Presentation Title",
            "Date",
            "Start Time",
            "End Time",
            "Track",
            "Session Format",
            "Room",
        ]
    )
    sheet.append(
        [
            3468947,
            "Agents Are Not Tasks",
            datetime(2026, 8, 4),
            time(16, 15),
            time(16, 35),
            "AI Agents: Foundation & Strategy",
            "Solo Talk",
            "Venetian Ballroom F",
        ]
    )
    content = io.BytesIO()
    workbook.save(content)

    raw = _parse_xlsx(content.getvalue())[0]
    values, errors = _prepare_row(raw, Event(name="Real XLSX", timezone="UTC"))

    assert errors == []
    assert values["presentation_code"] == "3468947"
    assert values["session_code"].startswith("import-composite:")
    assert values["session_title"].startswith("AI Agents: Foundation & Strategy")
    assert values["starts_at"] == "2026-08-04T16:15:00+00:00"
    assert values["ends_at"] == "2026-08-04T16:35:00+00:00"
    assert values["location_name"] == "Venetian Ballroom F"
