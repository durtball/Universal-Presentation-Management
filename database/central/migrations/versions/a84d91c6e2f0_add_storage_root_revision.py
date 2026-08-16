"""add the record revision required by the StorageRoot ORM

Revision ID: a84d91c6e2f0
Revises: f18a6c42d9e7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a84d91c6e2f0"
down_revision: str | Sequence[str] | None = "f18a6c42d9e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # StorageRoot inherits CentralRecordMixin. Existing rows need the same initial
    # revision value used by every other Central record before NOT NULL is enforced.
    op.add_column(
        "storage_roots",
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.alter_column("storage_roots", "revision", server_default=None)


def downgrade() -> None:
    op.drop_column("storage_roots", "revision")
