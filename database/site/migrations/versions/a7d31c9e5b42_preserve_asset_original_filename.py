"""Preserve the original intake filename on canonical presentation assets."""

import sqlalchemy as sa
from alembic import op

revision = "a7d31c9e5b42"
down_revision = "c52a819de740"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presentation_assets", sa.Column("original_filename", sa.String(1024)))
    op.execute("""
        UPDATE presentation_assets AS asset
        SET original_filename = media.original_filename
        FROM media_objects AS media
        WHERE media.media_object_id = asset.media_object_id
    """)


def downgrade() -> None:
    op.drop_column("presentation_assets", "original_filename")
