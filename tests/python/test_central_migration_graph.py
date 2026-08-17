"""Regression coverage for the Central Alembic merge graph."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MERGE_REVISION = "f18a6c42d9e7"
MERGE_PARENTS = {"5d23c80ab411", "d7f4a2c91b63"}
STORAGE_REVISION = "a84d91c6e2f0"
MEDIA_STORAGE_REVISION = "b93e4a71d520"
PREVIOUS_HEAD_REVISION = "c7a91e4b2d60"
HEAD_REVISION = "e19f4a7c2d31"


def central_script() -> ScriptDirectory:
    config = Config(REPOSITORY_ROOT / "database/central/alembic.ini")
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "database/central/migrations"))
    return ScriptDirectory.from_config(config)


def site_script() -> ScriptDirectory:
    config = Config(REPOSITORY_ROOT / "database/site/alembic.ini")
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "database/site/migrations"))
    return ScriptDirectory.from_config(config)


def test_central_migrations_have_one_merged_head() -> None:
    script = central_script()

    assert script.get_heads() == [HEAD_REVISION]
    head = script.get_revision(HEAD_REVISION)
    assert head is not None
    assert head.down_revision == PREVIOUS_HEAD_REVISION
    previous_head = script.get_revision(PREVIOUS_HEAD_REVISION)
    assert previous_head is not None
    assert previous_head.down_revision == MEDIA_STORAGE_REVISION
    media_storage = script.get_revision(MEDIA_STORAGE_REVISION)
    assert media_storage is not None
    assert media_storage.down_revision == STORAGE_REVISION
    storage = script.get_revision(STORAGE_REVISION)
    assert storage is not None
    assert storage.down_revision == MERGE_REVISION
    merge = script.get_revision(MERGE_REVISION)
    assert merge is not None
    assert set(merge.down_revision) == MERGE_PARENTS


def test_both_central_branches_converge_at_merge_revision() -> None:
    revisions = {revision.revision for revision in central_script().walk_revisions()}

    assert {
        HEAD_REVISION,
        PREVIOUS_HEAD_REVISION,
        MEDIA_STORAGE_REVISION,
        STORAGE_REVISION,
        MERGE_REVISION,
        *MERGE_PARENTS,
    } <= revisions


def test_storage_root_revision_upgrade_is_reversible() -> None:
    migration = central_script().get_revision(STORAGE_REVISION)

    assert migration is not None
    source = Path(migration.path).read_text()
    assert 'op.add_column(\n        "storage_roots"' in source
    assert 'sa.Column("revision", sa.Integer(), server_default="1", nullable=False)' in source
    assert 'op.drop_column("storage_roots", "revision")' in source


def test_site_media_storage_reference_has_one_head() -> None:
    script = site_script()
    assert script.get_heads() == ["d42f7a91c6e3"]
    assert script.get_revision("d42f7a91c6e3").down_revision == "c18d3f7a92e1"
