from uuid import uuid4

import pytest
from pydantic import ValidationError
from upm_signage.api import LayoutWrite
from upm_signage.auth import hash_password, verify


def test_bootstrap_password_verifier_is_salted_and_changeable():
    first = hash_password("admin")
    second = hash_password("admin")
    assert first != second
    assert "admin" not in first
    assert verify("admin", first)
    assert not verify("wrong", first)


def test_layout_rejects_unknown_or_out_of_canvas_elements():
    base = {
        "name": "Room door",
        "width": 1080,
        "height": 1920,
        "orientation": "portrait",
    }
    with pytest.raises(ValidationError, match="Unsupported element type"):
        LayoutWrite(
            **base,
            elements=[
                {
                    "element_id": uuid4(),
                    "type": "weather",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                }
            ],
        )
    with pytest.raises(ValidationError, match="inside the logical canvas"):
        LayoutWrite(
            **base,
            elements=[
                {
                    "element_id": uuid4(),
                    "type": "room_name",
                    "x": 1000,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                }
            ],
        )


def test_supported_layout_round_trips_exact_element_state():
    identifier = uuid4()
    value = LayoutWrite(
        name="Venetian G door",
        width=1080,
        height=1920,
        orientation="portrait",
        elements=[
            {
                "element_id": identifier,
                "type": "room_name",
                "x": 40,
                "y": 60,
                "width": 900,
                "height": 160,
                "opacity": 0.8,
                "style": {"font_size": 72, "color": "#ffffff"},
                "data_binding": {"field": "room_name"},
                "fallback_value": "Room",
            }
        ],
    )
    restored = LayoutWrite.model_validate_json(value.model_dump_json())
    assert restored.elements[0].element_id == identifier
    assert restored.elements[0].style["font_size"] == 72
    assert restored.elements[0].opacity == 0.8
