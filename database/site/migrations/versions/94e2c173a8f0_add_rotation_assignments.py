"""add Site rotating-slide defaults and overrides

Revision ID: 94e2c173a8f0
Revises: 7c4a91e2d5f0
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "94e2c173a8f0"
down_revision = "7c4a91e2d5f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rotation_assignments",
        sa.Column("rotation_assignment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("central_assignment_id", postgresql.UUID(as_uuid=True), unique=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_day", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column(
            "room_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rooms.room_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "presentation_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT"),
        ),
        sa.Column("source_authority", sa.String(16), nullable=False),
        sa.Column("override_state", sa.String(16), nullable=False, server_default="configured"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sync_state", sa.String(24), nullable=False, server_default="local"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope = 'event_day' AND room_id IS NULL AND session_id IS NULL) OR "
            "(scope = 'room_day' AND room_id IS NOT NULL AND session_id IS NULL) OR "
            "(scope = 'session' AND room_id IS NULL AND session_id IS NOT NULL)",
            name="rotation_assignment_valid_scope",
        ),
        sa.CheckConstraint(
            "source_authority IN ('central', 'site')",
            name="rotation_assignment_valid_authority",
        ),
    )
    op.create_index(
        "ix_site_rotation_effective",
        "rotation_assignments",
        ["event_id", "event_day", "room_id", "session_id", "source_authority", "active"],
    )


def downgrade() -> None:
    op.drop_table("rotation_assignments")
