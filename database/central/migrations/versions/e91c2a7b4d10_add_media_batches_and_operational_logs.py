"""add media import batches and operational logs

Revision ID: e91c2a7b4d10
Revises: c7a91e4b2d60
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e91c2a7b4d10"
down_revision: str | Sequence[str] | None = "c7a91e4b2d60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presentation_media_import_batches",
        sa.Column("batch_id", sa.UUID(), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("skipped_items", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_central_media_batches_event_created",
        "presentation_media_import_batches",
        ["event_id", "created_at"],
    )
    op.add_column("presentation_media_imports", sa.Column("batch_id", sa.UUID()))
    op.create_foreign_key(
        "fk_media_import_batch",
        "presentation_media_imports",
        "presentation_media_import_batches",
        ["batch_id"],
        ["batch_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_presentation_media_imports_batch_id", "presentation_media_imports", ["batch_id"]
    )
    _create_logs("central")


def _create_logs(_prefix: str) -> None:
    op.create_table(
        "operational_logs",
        sa.Column("operational_log_id", sa.UUID(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("message", sa.String(1024), nullable=False),
        sa.Column("batch_id", sa.UUID()),
        sa.Column("media_import_id", sa.UUID()),
        sa.Column("event_id", sa.UUID()),
        sa.Column("presentation_id", sa.UUID()),
        sa.Column("presentation_version_id", sa.UUID()),
        sa.Column("session_id", sa.UUID()),
        sa.Column("room_id", sa.UUID()),
        sa.Column("device_id", sa.UUID()),
        sa.Column("worker_id", sa.String(255)),
        sa.Column("correlation_id", sa.UUID()),
        sa.Column("context", postgresql.JSONB(), nullable=False),
    )
    for name, columns in (
        ("occurred", ["occurred_at"]),
        ("service_severity", ["service", "severity", "occurred_at"]),
        ("event_type", ["event_type", "occurred_at"]),
        ("batch", ["batch_id", "occurred_at"]),
        ("media_import", ["media_import_id", "occurred_at"]),
        ("event", ["event_id", "occurred_at"]),
    ):
        op.create_index(f"ix_central_logs_{name}", "operational_logs", columns)


def downgrade() -> None:
    op.drop_table("operational_logs")
    op.drop_index("ix_presentation_media_imports_batch_id", table_name="presentation_media_imports")
    op.drop_constraint("fk_media_import_batch", "presentation_media_imports", type_="foreignkey")
    op.drop_column("presentation_media_imports", "batch_id")
    op.drop_table("presentation_media_import_batches")
