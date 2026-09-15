"""Add revocable Site-scoped Signage service identities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e52b19a73c40"
down_revision = "a73c5e91f204"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "signage_installations",
        sa.Column("installation_id", postgresql.UUID(), primary_key=True),
        sa.Column("site_id", postgresql.UUID(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("credential_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "enrolled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["site_id"], ["sites.site_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_signage_installations_site_id", "signage_installations", ["site_id"])


def downgrade():
    op.drop_table("signage_installations")
