"""add Central Site registry and synchronization state

Revision ID: c91a4b2d8f20
Revises: ad875b02aaed
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c91a4b2d8f20"
down_revision: str | Sequence[str] | None = "ad875b02aaed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sites",
        sa.Column(
            "enrollment_state",
            sa.Enum(
                "unregistered",
                "pending",
                "active",
                "rejected",
                "revoked",
                "disabled",
                name="enrollmentstate",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column("sites", sa.Column("first_registered_at", sa.DateTime(timezone=True)))
    op.add_column("sites", sa.Column("last_registered_at", sa.DateTime(timezone=True)))
    op.add_column("sites", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.add_column("sites", sa.Column("last_successful_sync_at", sa.DateTime(timezone=True)))
    op.add_column("sites", sa.Column("application_version", sa.String(64)))
    op.add_column("sites", sa.Column("protocol_version", sa.Integer()))
    op.add_column("sites", sa.Column("reported_hostname", sa.String(255)))
    op.add_column("sites", sa.Column("reported_address", sa.String(255)))
    op.add_column(
        "sites",
        sa.Column(
            "capabilities",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "sites",
        sa.Column(
            "health_summary",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("sites", sa.Column("protocol_error", sa.String(255)))
    op.create_index(
        "ix_central_sites_enrollment_last_seen", "sites", ["enrollment_state", "last_seen_at"]
    )
    op.create_table(
        "site_enrollment_claims",
        sa.Column(
            "site_id",
            sa.UUID(),
            sa.ForeignKey("sites.site_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("claim_secret_hash", sa.String(64), nullable=False),
        sa.Column("poll_token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credential_delivered_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "site_credentials",
        sa.Column("credential_id", sa.UUID(), primary_key=True),
        sa.Column(
            "site_id", sa.UUID(), sa.ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_central_site_credentials_site_id", "site_credentials", ["site_id"])
    op.create_table(
        "sync_receipts",
        sa.Column("receipt_id", sa.UUID(), primary_key=True),
        sa.Column(
            "site_id",
            sa.UUID(),
            sa.ForeignKey("sites.site_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("site_id", "event_id", name="uq_central_sync_receipts_site_id"),
    )
    op.create_table(
        "sync_cursors",
        sa.Column(
            "site_id",
            sa.UUID(),
            sa.ForeignKey("sites.site_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("direction", sa.String(32), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_event_id", sa.UUID()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sync_sequences",
        sa.Column(
            "site_id",
            sa.UUID(),
            sa.ForeignKey("sites.site_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("next_value", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "site_managed_settings",
        sa.Column("setting_id", sa.UUID(), primary_key=True),
        sa.Column(
            "site_id", sa.UUID(), sa.ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("setting_key", sa.String(100), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "site_id", "setting_key", name="uq_central_site_managed_settings_site_id"
        ),
    )
    op.add_column(
        "outbox_events",
        sa.Column("protocol_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("outbox_events", sa.Column("source_sequence", sa.BigInteger()))
    op.add_column("outbox_events", sa.Column("correlation_id", sa.UUID()))
    op.add_column("outbox_events", sa.Column("causation_id", sa.UUID()))


def downgrade() -> None:
    for column in ("causation_id", "correlation_id", "source_sequence", "protocol_version"):
        op.drop_column("outbox_events", column)
    for table in (
        "site_managed_settings",
        "sync_sequences",
        "sync_cursors",
        "sync_receipts",
        "site_credentials",
        "site_enrollment_claims",
    ):
        op.drop_table(table)
    op.drop_index("ix_central_sites_enrollment_last_seen", table_name="sites")
    for column in (
        "protocol_error",
        "health_summary",
        "capabilities",
        "reported_address",
        "reported_hostname",
        "protocol_version",
        "application_version",
        "last_successful_sync_at",
        "last_seen_at",
        "last_registered_at",
        "first_registered_at",
        "enrollment_state",
    ):
        op.drop_column("sites", column)
