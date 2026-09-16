"""Persist the Signage installation identity and encrypted Site credential."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_signage"
down_revision = "0002_signage"
branch_labels = None
depends_on = None


def upgrade():
    if "installation" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "installation",
        sa.Column("installation_id", postgresql.UUID(), primary_key=True),
        sa.Column("singleton_key", sa.Integer(), nullable=False, unique=True),
        sa.Column("site_id", postgresql.UUID()),
        sa.Column("site_name", sa.String(255)),
        sa.Column("site_url", sa.String(2048)),
        sa.Column("credential_encrypted", sa.LargeBinary()),
        sa.Column("credential_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enrolled_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("installation")
