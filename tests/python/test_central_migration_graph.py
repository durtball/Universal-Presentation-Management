"""Regression coverage for the Central Alembic merge graph."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MERGE_REVISION = "f18a6c42d9e7"
MERGE_PARENTS = {"5d23c80ab411", "d7f4a2c91b63"}
HEAD_REVISION = "a84d91c6e2f0"


def central_script() -> ScriptDirectory:
    config = Config(REPOSITORY_ROOT / "database/central/alembic.ini")
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "database/central/migrations"))
    return ScriptDirectory.from_config(config)


def test_central_migrations_have_one_merged_head() -> None:
    script = central_script()

    assert script.get_heads() == [HEAD_REVISION]
    head = script.get_revision(HEAD_REVISION)
    assert head is not None
    assert head.down_revision == MERGE_REVISION
    merge = script.get_revision(MERGE_REVISION)
    assert merge is not None
    assert set(merge.down_revision) == MERGE_PARENTS


def test_both_central_branches_converge_at_merge_revision() -> None:
    revisions = {revision.revision for revision in central_script().walk_revisions()}

    assert {HEAD_REVISION, MERGE_REVISION, *MERGE_PARENTS} <= revisions


def test_storage_root_revision_upgrade_is_reversible() -> None:
    migration = central_script().get_revision(HEAD_REVISION)

    assert migration is not None
    source = Path(migration.path).read_text()
    assert 'op.add_column(\n        "storage_roots"' in source
    assert 'sa.Column("revision", sa.Integer(), server_default="1", nullable=False)' in source
    assert 'op.drop_column("storage_roots", "revision")' in source
