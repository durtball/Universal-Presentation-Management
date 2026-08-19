"""add SMB intake provenance

Revision ID: f31cd82ea507
Revises: e27bd42fa901
"""

from alembic import op
import sqlalchemy as sa

revision = "f31cd82ea507"
down_revision = "e27bd42fa901"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "media_objects",
        sa.Column("intake_origin", sa.String(32), nullable=False, server_default="browser"),
    )
    op.add_column("media_objects", sa.Column("source_actor", sa.String(255)))
    op.add_column("media_objects", sa.Column("source_share", sa.String(255)))


def downgrade():
    op.drop_column("media_objects", "source_share")
    op.drop_column("media_objects", "source_actor")
    op.drop_column("media_objects", "intake_origin")
