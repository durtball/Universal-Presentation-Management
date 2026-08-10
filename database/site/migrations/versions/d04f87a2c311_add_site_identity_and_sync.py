"""add permanent Site identity and synchronization state

Revision ID: d04f87a2c311
Revises: b7c4e2a91f10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d04f87a2c311"
down_revision: str | Sequence[str] | None = "b7c4e2a91f10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_site_identity",
        sa.Column(
            "site_id",
            sa.UUID(),
            sa.ForeignKey("sites.site_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("singleton_key", sa.Integer(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("installation_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton_key = 1", name="singleton"),
    )
    op.create_table(
        "central_registration",
        sa.Column(
            "site_id",
            sa.UUID(),
            sa.ForeignKey("sites.site_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("central_url", sa.String(2048)),
        sa.Column(
            "state",
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
            server_default="unregistered",
            nullable=False,
        ),
        sa.Column("claim_secret_encrypted", sa.LargeBinary()),
        sa.Column("poll_token_encrypted", sa.LargeBinary()),
        sa.Column("credential_encrypted", sa.LargeBinary()),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_connection_at", sa.DateTime(timezone=True)),
        sa.Column("protocol_compatible", sa.Boolean()),
        sa.Column("last_error", sa.String(2048)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sync_receipts",
        sa.Column("receipt_id", sa.UUID(), primary_key=True),
        sa.Column("event_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sync_cursors",
        sa.Column("direction", sa.String(32), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_event_id", sa.UUID()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sync_sequences",
        sa.Column("singleton_key", sa.Integer(), primary_key=True),
        sa.Column("next_value", sa.BigInteger(), nullable=False),
    )
    op.execute("INSERT INTO sync_sequences (singleton_key, next_value) VALUES (1, 1)")
    op.create_table(
        "managed_settings",
        sa.Column("setting_key", sa.String(100), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("central_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
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
        "managed_settings",
        "sync_sequences",
        "sync_cursors",
        "sync_receipts",
        "central_registration",
        "local_site_identity",
    ):
        op.drop_table(table)
