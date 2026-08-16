"""Add media source relative path provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c18d3f7a92e1"
down_revision: str | Sequence[str] | None = "ab73d10e5c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_objects",
        sa.Column("source_relative_path", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media_objects", "source_relative_path")
