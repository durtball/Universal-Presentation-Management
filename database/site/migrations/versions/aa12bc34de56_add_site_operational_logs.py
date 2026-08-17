"""add Site-owned operational logs

Revision ID: aa12bc34de56
Revises: d42f7a91c6e3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "aa12bc34de56"
down_revision: str | Sequence[str] | None = "d42f7a91c6e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        op.create_index(f"ix_site_logs_{name}", "operational_logs", columns)


def downgrade() -> None:
    op.drop_table("operational_logs")
