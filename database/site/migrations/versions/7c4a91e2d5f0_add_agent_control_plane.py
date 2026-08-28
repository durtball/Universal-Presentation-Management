"""add durable Agent control plane and review saveback state

Revision ID: 7c4a91e2d5f0
Revises: 2f6c1e9a4b70
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7c4a91e2d5f0"
down_revision = "2f6c1e9a4b70"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("agent_token_hash", sa.String(64)))
    op.create_unique_constraint("uq_site_devices_agent_token_hash", "devices", ["agent_token_hash"])
    op.create_table(
        "device_runtime_states",
        sa.Column("device_id", sa.UUID(), primary_key=True),
        sa.Column("connected_at", sa.DateTime(timezone=True)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("windows_version", sa.String(255)),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("interactive_session_available", sa.Boolean(), nullable=False),
        sa.Column("powerpoint_available", sa.Boolean()),
        sa.Column("powerpoint_version", sa.String(64)),
        sa.Column("free_disk_bytes", sa.BigInteger()),
        sa.Column("local_cache_bytes", sa.BigInteger()),
        sa.Column("current_presentation_id", sa.UUID()),
        sa.Column("current_review_session_id", sa.UUID()),
        sa.Column("current_command_id", sa.UUID()),
        sa.Column("last_error", sa.String(2048)),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
    )
    op.create_table(
        "device_commands",
        sa.Column("command_id", sa.UUID(), primary_key=True),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("room_id", sa.UUID()),
        sa.Column("command_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.String(2048)),
        sa.Column("correlation_id", sa.UUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.site_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.room_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("site_id", "idempotency_key"),
    )
    op.create_index(
        "ix_site_device_commands_delivery",
        "device_commands",
        ["device_id", "status", "available_at"],
    )
    op.create_index("ix_site_device_commands_correlation", "device_commands", ["correlation_id"])
    op.create_table(
        "device_command_attempts",
        sa.Column("attempt_id", sa.UUID(), primary_key=True),
        sa.Column("command_id", sa.UUID(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_status", sa.String(16)),
        sa.Column("result_at", sa.DateTime(timezone=True)),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["command_id"], ["device_commands.command_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("command_id", "attempt_number"),
    )
    op.create_table(
        "presentation_review_sessions",
        sa.Column("review_session_id", sa.UUID(), primary_key=True),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("presentation_id", sa.UUID(), nullable=False),
        sa.Column("base_presentation_version_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("room_id", sa.UUID(), nullable=False),
        sa.Column("operator_id", sa.String(255), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("local_changes_at", sa.DateTime(timezone=True)),
        sa.Column("working_filename", sa.String(1024)),
        sa.Column("working_size_bytes", sa.BigInteger()),
        sa.Column("working_sha256", sa.String(64)),
        sa.Column("working_modified_at", sa.DateTime(timezone=True)),
        sa.Column("saveback_media_object_id", sa.UUID()),
        sa.Column("saveback_version_id", sa.UUID()),
        sa.Column("conflict_version_id", sa.UUID()),
        sa.Column("correlation_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["site_id"], ["sites.site_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["presentation_id"], ["presentations.presentation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["base_presentation_version_id"],
            ["presentation_versions.presentation_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.room_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["saveback_media_object_id"], ["media_objects.media_object_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["saveback_version_id"],
            ["presentation_versions.presentation_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conflict_version_id"],
            ["presentation_versions.presentation_version_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_site_review_sessions_state",
        "presentation_review_sessions",
        ["site_id", "state", "opened_at"],
    )


def downgrade():
    op.drop_index("ix_site_review_sessions_state", table_name="presentation_review_sessions")
    op.drop_table("presentation_review_sessions")
    op.drop_table("device_command_attempts")
    op.drop_index("ix_site_device_commands_correlation", table_name="device_commands")
    op.drop_index("ix_site_device_commands_delivery", table_name="device_commands")
    op.drop_table("device_commands")
    op.drop_table("device_runtime_states")
    op.drop_constraint("uq_site_devices_agent_token_hash", "devices", type_="unique")
    op.drop_column("devices", "agent_token_hash")
