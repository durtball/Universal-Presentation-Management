"""Regression coverage for the Central Alembic merge graph."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MERGE_REVISION = "f18a6c42d9e7"
MERGE_PARENTS = {"5d23c80ab411", "d7f4a2c91b63"}


def central_script() -> ScriptDirectory:
    config = Config(REPOSITORY_ROOT / "database/central/alembic.ini")
    config.set_main_option(
        "script_location", str(REPOSITORY_ROOT / "database/central/migrations")
    )
    return ScriptDirectory.from_config(config)


def test_central_migrations_have_one_merged_head() -> None:
    script = central_script()

    assert script.get_heads() == [MERGE_REVISION]
    merge = script.get_revision(MERGE_REVISION)
    assert merge is not None
    assert set(merge.down_revision) == MERGE_PARENTS


def test_both_central_branches_converge_at_merge_revision() -> None:
    revisions = {revision.revision for revision in central_script().walk_revisions()}

    assert {MERGE_REVISION, *MERGE_PARENTS} <= revisions
