"""add durable Central media replication receivers

Revision ID: bd84e21f6a53
Revises: ec914f027b3a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bd84e21f6a53"
down_revision: str | Sequence[str] | None = "ec914f027b3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_replication_receive_sessions",
        sa.Column("replication_session_id", sa.UUID(), primary_key=True),
        sa.Column("origin_site_id", sa.UUID(), sa.ForeignKey("sites.site_id"), nullable=False),
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
        sa.Column("source_media_object_id", sa.UUID(), nullable=False),
        sa.Column("presentation_identifier", sa.String(128), nullable=False),
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("canonical_filename", sa.String(1024)),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(255)),
        sa.Column("partial_key", sa.String(255), nullable=False, unique=True),
        sa.Column("confirmed_offset", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("state", sa.String(16), server_default="queued", nullable=False),
        sa.Column("replication_state", sa.String(16), server_default="queued", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_progress_at", sa.DateTime(timezone=True)),
        sa.Column("error_detail", sa.String(2048)),
        sa.Column(
            "finalized_media_object_id",
            sa.UUID(),
            sa.ForeignKey("media_object_replicas.media_object_id", ondelete="RESTRICT"),
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "expected_size >= 0",
            name="ck_media_replication_receive_sessions_expected_size_nonnegative",
        ),
        sa.CheckConstraint(
            "confirmed_offset >= 0 AND confirmed_offset <= expected_size",
            name="ck_media_replication_receive_sessions_confirmed_offset_range",
        ),
    )
    op.create_index(
        "ix_central_replication_site_state",
        "media_replication_receive_sessions",
        ["origin_site_id", "state"],
    )
    op.create_index(
        "ix_central_replication_version",
        "media_replication_receive_sessions",
        ["presentation_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_central_replication_version", table_name="media_replication_receive_sessions")
    op.drop_index(
        "ix_central_replication_site_state", table_name="media_replication_receive_sessions"
    )
    op.drop_table("media_replication_receive_sessions")
