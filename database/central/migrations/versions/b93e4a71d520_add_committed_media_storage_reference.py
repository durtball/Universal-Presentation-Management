"""add committed Media Storage references to Central imports

Revision ID: b93e4a71d520
Revises: a84d91c6e2f0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b93e4a71d520"
down_revision: str | Sequence[str] | None = "a84d91c6e2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presentation_media_imports", sa.Column("committed_storage_root_id", sa.UUID()))
    op.add_column("presentation_media_imports", sa.Column("committed_storage_key", sa.String(2048)))
    op.create_foreign_key(
        "fk_media_import_committed_storage_root",
        "presentation_media_imports",
        "storage_roots",
        ["committed_storage_root_id"],
        ["storage_root_id"],
        ondelete="RESTRICT",
    )
    op.add_column("media_replication_receive_sessions", sa.Column("storage_target_id", sa.UUID()))


def downgrade() -> None:
    op.drop_column("media_replication_receive_sessions", "storage_target_id")
    op.drop_constraint(
        "fk_media_import_committed_storage_root",
        "presentation_media_imports",
        type_="foreignkey",
    )
    op.drop_column("presentation_media_imports", "committed_storage_key")
    op.drop_column("presentation_media_imports", "committed_storage_root_id")
