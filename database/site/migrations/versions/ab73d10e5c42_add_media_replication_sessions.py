"""add durable Site media replication sessions

Revision ID: ab73d10e5c42
Revises: f29c7d10a4e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ab73d10e5c42"
down_revision: str | Sequence[str] | None = "f29c7d10a4e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_replication_sessions",
        sa.Column("replication_session_id", sa.UUID(), primary_key=True),
        sa.Column("site_id", sa.UUID(), sa.ForeignKey("sites.site_id"), nullable=False),
        sa.Column("event_id", sa.UUID(), sa.ForeignKey("events.event_id"), nullable=False),
        sa.Column(
            "presentation_id",
            sa.UUID(),
            sa.ForeignKey("presentations.presentation_id"),
            nullable=False,
        ),
        sa.Column(
            "presentation_version_id",
            sa.UUID(),
            sa.ForeignKey("presentation_versions.presentation_version_id"),
            nullable=False,
        ),
        sa.Column(
            "media_object_id",
            sa.UUID(),
            sa.ForeignKey("media_objects.media_object_id"),
            nullable=False,
        ),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("canonical_filename", sa.String(1024)),
        sa.Column("media_type", sa.String(255)),
        sa.Column("confirmed_offset", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("state", sa.String(16), server_default="queued", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_progress_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(2048)),
        sa.Column("central_media_object_id", sa.UUID()),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("sync_state", sa.String(24), server_default="local", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("media_object_id", "presentation_version_id"),
        sa.CheckConstraint(
            "expected_size >= 0", name="ck_media_replication_sessions_expected_size_nonnegative"
        ),
        sa.CheckConstraint(
            "confirmed_offset >= 0 AND confirmed_offset <= expected_size",
            name="ck_media_replication_sessions_confirmed_offset_range",
        ),
    )
    op.create_index(
        "ix_site_replication_state_progress",
        "media_replication_sessions",
        ["state", "last_progress_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_site_replication_state_progress", table_name="media_replication_sessions")
    op.drop_table("media_replication_sessions")
