"""add Site-specific imported room mappings

Revision ID: b32d8e0f5a21
Revises: a21c7d9e4f10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b32d8e0f5a21"
down_revision: str | Sequence[str] | None = "a21c7d9e4f10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_room_mappings",
        sa.Column("site_room_mapping_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("imported_label", sa.String(length=255), nullable=False),
        sa.Column("normalized_imported_label", sa.String(length=255), nullable=False),
        sa.Column("target_room_id", sa.UUID(), nullable=True),
        sa.Column("target_room_label", sa.String(length=255), nullable=True),
        sa.Column("mapping_status", sa.String(length=16), nullable=False),
        sa.Column("confirmed_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.site_id"],
            name=op.f("fk_central_site_room_mappings_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "site_room_mapping_id", name=op.f("pk_central_site_room_mappings")
        ),
        sa.UniqueConstraint(
            "site_id", "normalized_imported_label",
            name=op.f("uq_central_site_room_mappings_site_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("site_room_mappings")
