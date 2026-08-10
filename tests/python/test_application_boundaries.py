"""Central/Site application and persistence boundary tests."""

from pathlib import Path

from upm_central.api import create_app as create_central_app
from upm_central.persistence import models as central_models  # noqa: F401
from upm_central.persistence.base import CentralBase
from upm_site.api import create_app as create_site_app
from upm_site.persistence import models as site_models  # noqa: F401
from upm_site.persistence.base import SiteBase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_central_and_site_are_distinct_fastapi_applications() -> None:
    central_app = create_central_app()
    site_app = create_site_app()

    assert central_app is not site_app
    assert central_app.openapi()["info"]["title"] == "UPM Central API"
    assert site_app.openapi()["info"]["title"] == "UPM Site API"


def test_central_and_site_metadata_are_separate() -> None:
    assert CentralBase.metadata is not SiteBase.metadata
    assert "persons" in CentralBase.metadata.tables
    assert "person_projections" not in CentralBase.metadata.tables
    assert "person_projections" in SiteBase.metadata.tables
    assert "storage_targets" not in CentralBase.metadata.tables
    assert "storage_targets" in SiteBase.metadata.tables


def test_each_metadata_context_has_only_local_foreign_keys() -> None:
    for metadata in (CentralBase.metadata, SiteBase.metadata):
        table_names = set(metadata.tables)
        foreign_targets = {
            foreign_key.target_fullname.split(".")[0]
            for table in metadata.tables.values()
            for foreign_key in table.foreign_keys
        }
        assert foreign_targets <= table_names


def test_migration_histories_and_version_tables_are_isolated() -> None:
    central_env = (REPOSITORY_ROOT / "database/central/migrations/env.py").read_text()
    site_env = (REPOSITORY_ROOT / "database/site/migrations/env.py").read_text()

    assert "upm_central" in central_env
    assert "upm_site" not in central_env
    assert 'VERSION_TABLE = "alembic_version_central"' in central_env
    assert "upm_site" in site_env
    assert "upm_central" not in site_env
    assert 'VERSION_TABLE = "alembic_version_site"' in site_env
