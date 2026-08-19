"""add offline Site users and SMB policy"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e27bd42fa901"
down_revision = "bc34de56fa78"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("user_id", sa.UUID(), primary_key=True),
        sa.Column("central_user_id", sa.UUID(), unique=True),
        sa.Column("user_type", sa.String(32), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("normalized_username", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("web_password_hash", sa.Text()),
        sa.Column("roles", postgresql.JSONB(), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("web_access", sa.Boolean(), nullable=False),
        sa.Column("smb_enabled", sa.Boolean(), nullable=False),
        sa.Column("smb_credential_revision", sa.Integer(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("smb_last_activity_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("users")
