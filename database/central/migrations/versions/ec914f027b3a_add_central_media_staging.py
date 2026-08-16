"""add durable Central presentation-media staging

Revision ID: ec914f027b3a
Revises: da7c1b9e4201
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ec914f027b3a"
down_revision: str | Sequence[str] | None = "da7c1b9e4201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presentation_media_imports",
        sa.Column("media_import_id", sa.UUID(), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "destination_site_id", sa.UUID(), sa.ForeignKey("sites.site_id", ondelete="RESTRICT")
        ),
        sa.Column(
            "presentation_id",
            sa.UUID(),
            sa.ForeignKey("presentations.presentation_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "presentation_version_id",
            sa.UUID(),
            sa.ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT"),
        ),
        sa.Column("presentation_identifier", sa.String(128)),
        sa.Column("external_presentation_id", sa.String(512)),
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("canonical_filename", sa.String(1024)),
        sa.Column("staging_key", sa.String(255), nullable=False, unique=True),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("mime_type", sa.String(255)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("match_state", sa.String(24), nullable=False),
        sa.Column("match_reason", sa.String(1024)),
        sa.Column("match_candidates", postgresql.JSONB(), nullable=False),
        sa.Column("import_state", sa.String(24), nullable=False),
        sa.Column("sync_state", sa.String(24), nullable=False),
        sa.Column(
            "transfer_job_id",
            sa.UUID(),
            sa.ForeignKey("transfer_jobs.transfer_job_id", ondelete="RESTRICT"),
        ),
        sa.Column("site_media_object_id", sa.UUID()),
        sa.Column("idempotency_key", sa.String(255), unique=True),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_detail", sa.String(2048)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_nonnegative"),
        sa.CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        sa.CheckConstraint(
            "match_state IN ('exact','high_confidence','ambiguous','unmatched','manual')",
            name="mediamatchstate",
        ),
        sa.CheckConstraint(
            "import_state IN ('uploading','staged','needs_review','assigned',"
            "'transfer_queued','transferring','site_ready','retry_wait','failed','cancelled')",
            name="mediaimportstate",
        ),
        sa.CheckConstraint(
            "sync_state IN ('local','pending','synchronizing','synchronized','conflict','failed')",
            name="syncstate",
        ),
    )
    op.create_index(
        "ix_central_media_import_event_state",
        "presentation_media_imports",
        ["event_id", "import_state"],
    )
    op.create_index(
        "ix_central_media_import_presentation", "presentation_media_imports", ["presentation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_central_media_import_presentation", table_name="presentation_media_imports")
    op.drop_index("ix_central_media_import_event_state", table_name="presentation_media_imports")
    op.drop_table("presentation_media_imports")
