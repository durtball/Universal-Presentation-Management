"""Preserve the original intake filename on canonical presentation assets."""

import sqlalchemy as sa
from alembic import op

revision = "c4f8a2d91e60"
down_revision = "e4a7c921bd30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presentation_assets", sa.Column("original_filename", sa.String(1024)))
    op.execute("""
        UPDATE presentation_assets AS asset
        SET original_filename = source.original_filename
        FROM (
            SELECT DISTINCT ON (presentation_version_id)
                presentation_version_id, original_filename
            FROM presentation_media_imports
            WHERE presentation_version_id IS NOT NULL
            ORDER BY presentation_version_id, confirmed_at DESC NULLS LAST, created_at DESC
        ) AS source
        WHERE source.presentation_version_id = asset.presentation_version_id
    """)
    op.execute("""
        UPDATE presentation_assets AS asset
        SET original_filename = receiver.original_filename
        FROM media_replication_receive_sessions AS receiver
        WHERE receiver.presentation_version_id = asset.presentation_version_id
          AND asset.original_filename IS NULL
    """)


def downgrade() -> None:
    op.drop_column("presentation_assets", "original_filename")
