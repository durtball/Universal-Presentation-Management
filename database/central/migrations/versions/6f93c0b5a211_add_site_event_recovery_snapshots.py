"""add complete Site Event recovery snapshots

Revision ID: 6f93c0b5a211
Revises: 31d9a7c42e10
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6f93c0b5a211"
down_revision: str | None = "31d9a7c42e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_event_recovery_snapshots",
        sa.Column("recovery_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("site_event_revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("site_event_revision >= 1", name="site_event_revision_positive"),
        sa.ForeignKeyConstraint(["event_id"], ["events.event_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.site_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("recovery_snapshot_id"),
        sa.UniqueConstraint("site_id", "event_id"),
    )
    op.create_index(
        "ix_central_site_event_recovery_event",
        "site_event_recovery_snapshots",
        ["event_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_central_site_event_recovery_event", table_name="site_event_recovery_snapshots"
    )
    op.drop_table("site_event_recovery_snapshots")
