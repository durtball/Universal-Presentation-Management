"""add Media Storage reference to Site pull sessions

Revision ID: d42f7a91c6e3
Revises: c18d3f7a92e1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d42f7a91c6e3"
down_revision: str | Sequence[str] | None = "c18d3f7a92e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_transfer_sessions", sa.Column("storage_target_id", sa.UUID()))


def downgrade() -> None:
    op.drop_column("media_transfer_sessions", "storage_target_id")
