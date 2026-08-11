"""Fast import parsing and header-detection regressions."""

import pytest
from fastapi import HTTPException

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
