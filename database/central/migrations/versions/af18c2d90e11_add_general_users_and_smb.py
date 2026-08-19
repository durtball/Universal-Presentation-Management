"""extend Central users with general access and SMB policy"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "af18c2d90e11"
down_revision = "e91c2a7b4d10"
branch_labels = None
depends_on = None


def upgrade():
    columns = [
        sa.Column("email", sa.String(320)),
        sa.Column(
            "permissions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("web_access", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("smb_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("smb_credential_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("smb_last_activity_at", sa.DateTime(timezone=True)),
        sa.Column(
            "site_scope", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    ]
    for c in columns:
        op.add_column("admin_users", c)


def downgrade():
    for n in reversed(
        [
            "email",
            "permissions",
            "web_access",
            "smb_enabled",
            "smb_credential_revision",
            "smb_last_activity_at",
            "site_scope",
        ]
    ):
        op.drop_column("admin_users", n)
