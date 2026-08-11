"""add Central administrator authentication

Revision ID: a21c7d9e4f10
Revises: 7b2d9a4e6c11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a21c7d9e4f10"
down_revision: str | Sequence[str] | None = "7b2d9a4e6c11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("admin_user_id", sa.UUID(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("normalized_username", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("admin_user_id", name=op.f("pk_central_admin_users")),
        sa.UniqueConstraint(
            "normalized_username", name=op.f("uq_central_admin_users_normalized_username")
        ),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("admin_session_id", sa.UUID(), nullable=False),
        sa.Column("admin_user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remote_address", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["admin_users.admin_user_id"],
            name=op.f("fk_central_admin_sessions_admin_user_id_admin_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("admin_session_id", name=op.f("pk_central_admin_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_central_admin_sessions_token_hash")),
    )
    op.create_index("ix_admin_sessions_expiry", "admin_sessions", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_admin_sessions_expiry", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_table("admin_users")
