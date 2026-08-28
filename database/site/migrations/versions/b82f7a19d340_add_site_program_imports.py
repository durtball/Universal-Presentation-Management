"""add durable Site program import staging

Revision ID: b82f7a19d340
Revises: 94e2c173a8f0
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b82f7a19d340"
down_revision: str | None = "94e2c173a8f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "program_import_sources",
        sa.Column("import_source_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(8), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_type IN ('csv', 'xlsx')", name="importsourcetype"),
        sa.PrimaryKeyConstraint("import_source_id"),
    )
    op.create_table(
        "program_import_batches",
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("import_source_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(1024), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("importer_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("valid_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("committed_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("failure_summary", sa.Text()),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('uploaded', 'parsing', 'staged', 'review', 'ready', "
            "'committing', 'committed', 'failed', 'cancelled')",
            name="importstatus",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.event_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["import_source_id"], ["program_import_sources.import_source_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("import_batch_id"),
        sa.UniqueConstraint("event_id", "source_sha256", "importer_type"),
    )
    op.create_index(
        "ix_site_program_import_batches_event_status",
        "program_import_batches",
        ["event_id", "status"],
    )
    op.create_table(
        "program_import_rows",
        sa.Column("import_row_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_row_identity", sa.String(64), nullable=False),
        sa.Column("raw_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("corrected_values", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("validation_state", sa.String(16), nullable=False),
        sa.Column("validation_messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("committed_entity_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('person', 'participant', 'session', 'presentation', "
            "'relationship', 'unknown')",
            name="importentitytype",
        ),
        sa.CheckConstraint(
            "validation_state IN ('pending', 'valid', 'warning', 'error')",
            name="importvalidationstate",
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"], ["program_import_batches.import_batch_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("import_row_id"),
        sa.UniqueConstraint("import_batch_id", "source_row_number"),
    )
    op.create_index(
        "ix_site_program_import_rows_batch_state",
        "program_import_rows",
        ["import_batch_id", "validation_state"],
    )


def downgrade() -> None:
    op.drop_index("ix_site_program_import_rows_batch_state", table_name="program_import_rows")
    op.drop_table("program_import_rows")
    op.drop_index(
        "ix_site_program_import_batches_event_status", table_name="program_import_batches"
    )
    op.drop_table("program_import_batches")
    op.drop_table("program_import_sources")
