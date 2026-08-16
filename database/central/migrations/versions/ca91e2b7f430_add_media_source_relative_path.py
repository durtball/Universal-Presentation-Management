"""Add presentation import source relative path provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ca91e2b7f430"
down_revision: str | Sequence[str] | None = "bd84e21f6a53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "presentation_media_imports",
        sa.Column("source_relative_path", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("presentation_media_imports", "source_relative_path")
