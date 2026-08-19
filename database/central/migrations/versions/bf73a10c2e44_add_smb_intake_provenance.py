"""add SMB intake provenance

Revision ID: bf73a10c2e44
Revises: af18c2d90e11
"""

from alembic import op
import sqlalchemy as sa

revision = "bf73a10c2e44"
down_revision = "af18c2d90e11"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("presentation_media_imports", sa.Column("source_actor", sa.String(255)))
    op.add_column("presentation_media_imports", sa.Column("source_share", sa.String(255)))


def downgrade():
    op.drop_column("presentation_media_imports", "source_share")
    op.drop_column("presentation_media_imports", "source_actor")
