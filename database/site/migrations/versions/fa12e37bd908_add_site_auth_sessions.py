"""add Site authentication sessions and lockout state"""

from alembic import op
import sqlalchemy as sa

revision = "fa12e37bd908"
down_revision = "f31cd82ea507"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True)))
    op.create_table(
        "user_sessions",
        sa.Column("user_session_id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("user_sessions")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
