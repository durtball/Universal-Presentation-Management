"""PostgreSQL-only checks for independent migrated schemas."""

import os

import pytest
from sqlalchemy import create_engine, inspect, text

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")
SITE_URL = os.getenv("UPM_SITE_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not CENTRAL_URL or not SITE_URL,
        reason="independent Central and Site PostgreSQL URLs are required",
    ),
]


def test_postgres_databases_and_version_histories_are_independent() -> None:
    assert CENTRAL_URL != SITE_URL
    central_engine = create_engine(CENTRAL_URL)
    site_engine = create_engine(SITE_URL)
    try:
        central_tables = set(inspect(central_engine).get_table_names())
        site_tables = set(inspect(site_engine).get_table_names())

        assert "alembic_version_central" in central_tables
        assert "alembic_version_site" not in central_tables
        assert "persons" in central_tables
        assert "storage_targets" not in central_tables

        assert "alembic_version_site" in site_tables
        assert "alembic_version_central" not in site_tables
        assert "person_projections" in site_tables
        assert "storage_targets" in site_tables
        assert "persons" not in site_tables
    finally:
        central_engine.dispose()
        site_engine.dispose()


@pytest.mark.parametrize(
    ("url", "table_name", "id_column"),
    [
        (CENTRAL_URL, "persons", "person_id"),
        (SITE_URL, "storage_targets", "storage_target_id"),
    ],
)
def test_entity_identifiers_use_postgresql_native_uuid(
    url: str, table_name: str, id_column: str
) -> None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            data_type = connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table_name "
                    "AND column_name = :column_name"
                ),
                {"table_name": table_name, "column_name": id_column},
            ).scalar_one()
        assert data_type == "uuid"
    finally:
        engine.dispose()


def test_permanent_person_relationships_use_restrictive_deletion() -> None:
    engine = create_engine(CENTRAL_URL)
    try:
        foreign_keys = inspect(engine).get_foreign_keys("event_participations")
        person_key = next(key for key in foreign_keys if key["referred_table"] == "persons")
        assert person_key["options"]["ondelete"] == "RESTRICT"
    finally:
        engine.dispose()


def test_site_storage_constraints_are_present() -> None:
    engine = create_engine(SITE_URL)
    try:
        constraints = {
            item["name"] for item in inspect(engine).get_check_constraints("storage_targets")
        }
        assert "ck_site_storage_targets_root_path_absolute" in constraints
        assert "ck_site_storage_targets_threshold_order" in constraints
    finally:
        engine.dispose()
