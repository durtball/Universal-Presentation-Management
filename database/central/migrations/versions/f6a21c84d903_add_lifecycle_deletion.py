"""add durable lifecycle deletion and retained person history

Revision ID: f6a21c84d903
Revises: b32d8e0f5a21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a21c84d903"
down_revision: str | Sequence[str] | None = "b32d8e0f5a21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retained_person_history",
        sa.Column("retained_history_id", sa.UUID(), primary_key=True),
        sa.Column(
            "person_id",
            sa.UUID(),
            sa.ForeignKey("persons.person_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_event_id", sa.UUID(), nullable=False),
        sa.Column("event_name", sa.String(255), nullable=False),
        sa.Column(
            "participation_summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("retained_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_retained_person_history_person_id", "retained_person_history", ["person_id"]
    )
    op.create_index(
        "ix_retained_person_history_source_event_id", "retained_person_history", ["source_event_id"]
    )
    op.create_table(
        "deletion_operations",
        sa.Column("deletion_operation_id", sa.UUID(), primary_key=True),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("target_display_name", sa.String(255), nullable=False),
        sa.Column("initiated_by", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column(
            "dependency_counts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "site_statuses",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "media_results",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(2048)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("target_type", "target_id"),
    )


def downgrade() -> None:
    op.drop_table("deletion_operations")
    op.drop_index(
        "ix_retained_person_history_source_event_id", table_name="retained_person_history"
    )
    op.drop_index("ix_retained_person_history_person_id", table_name="retained_person_history")
    op.drop_table("retained_person_history")
