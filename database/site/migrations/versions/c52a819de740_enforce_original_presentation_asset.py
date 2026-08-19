"""Enforce one asset of each kind per PresentationVersion.

Revision ID: c52a819de740
Revises: fa12e37bd908
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c52a819de740"
down_revision: str | Sequence[str] | None = "fa12e37bd908"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_site_presentation_assets_original_version",
        "presentation_assets",
        ["presentation_version_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'original'"),
    )


def downgrade() -> None:
    op.drop_index("uq_site_presentation_assets_original_version", table_name="presentation_assets")
