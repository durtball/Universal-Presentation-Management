"""Identifier behavior and validation tests."""

from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from upm_shared.identifiers import UUID7, new_uuid7


class IdentifierEnvelope(BaseModel):
    identifier: UUID7


def test_uuid7_generation_is_ordered_and_unique() -> None:
    identifiers = [new_uuid7() for _ in range(100)]

    assert len(set(identifiers)) == len(identifiers)
    assert all(identifier.version == 7 for identifier in identifiers)
    assert identifiers == sorted(identifiers)


def test_uuid7_contract_rejects_other_uuid_versions() -> None:
    with pytest.raises(ValidationError):
        IdentifierEnvelope(identifier=uuid4())


def test_uuid7_contract_json_schema_is_language_neutral() -> None:
    schema = IdentifierEnvelope.model_json_schema()

    reference = schema["properties"]["identifier"]["$ref"]
    definition_name = reference.rsplit("/", maxsplit=1)[-1]
    assert schema["$defs"][definition_name]["format"] == "uuid"
