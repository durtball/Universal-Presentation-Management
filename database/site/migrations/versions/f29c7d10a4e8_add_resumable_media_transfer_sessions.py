"""add durable Site resumable media transfer sessions

Revision ID: f29c7d10a4e8
Revises: e8b91c4a620d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f29c7d10a4e8"
down_revision: str | Sequence[str] | None = "e8b91c4a620d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_transfer_sessions",
        sa.Column("transfer_session_id", sa.UUID(), primary_key=True),
        sa.Column(
            "site_id",
            sa.UUID(),
            sa.ForeignKey("sites.site_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "presentation_id",
            sa.UUID(),
            sa.ForeignKey("presentations.presentation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "presentation_version_id",
            sa.UUID(),
            sa.ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("canonical_filename", sa.String(1024), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(255)),
        sa.Column("partial_key", sa.String(255), nullable=False, unique=True),
        sa.Column("confirmed_offset", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_progress_at", sa.DateTime(timezone=True)),
        sa.Column("error_detail", sa.String(2048)),
        sa.Column(
            "media_object_id",
            sa.UUID(),
            sa.ForeignKey("media_objects.media_object_id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("expected_size >= 0", name="expected_size_nonnegative"),
        sa.CheckConstraint(
            "confirmed_offset >= 0 AND confirmed_offset <= expected_size",
            name="confirmed_offset_range",
        ),
        sa.CheckConstraint(
            "state IN ('queued','available','transferring','retry_wait','verifying',"
            "'completed','failed','cancelled','expired')",
            name="mediatransferstate",
        ),
    )
    op.create_index(
        "ix_site_media_transfer_state_progress",
        "media_transfer_sessions",
        ["state", "last_progress_at"],
    )
    op.create_index(
        "ix_site_media_transfer_presentation_version",
        "media_transfer_sessions",
        ["presentation_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_site_media_transfer_presentation_version", table_name="media_transfer_sessions"
    )
    op.drop_index("ix_site_media_transfer_state_progress", table_name="media_transfer_sessions")
    op.drop_table("media_transfer_sessions")
