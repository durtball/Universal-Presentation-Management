"""Allow Central-owned canonical media and enforce original asset identity.

Revision ID: e4a7c921bd30
Revises: bf73a10c2e44
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a7c921bd30"
down_revision: str | Sequence[str] | None = "bf73a10c2e44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("media_object_replicas", "authoritative_site_id", nullable=True)
    op.create_index(
        "uq_central_presentation_assets_original_version",
        "presentation_assets",
        ["presentation_version_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'original'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_central_presentation_assets_original_version", table_name="presentation_assets"
    )
    op.alter_column("media_object_replicas", "authoritative_site_id", nullable=False)
