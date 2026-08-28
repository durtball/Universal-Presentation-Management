"""add Central rotating-slide assignments

Revision ID: 31d9a7c42e10
Revises: fe21a4c8d901
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "31d9a7c42e10"
down_revision = "fe21a4c8d901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rotation_assignments",
        sa.Column("rotation_assignment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_day", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("room_id", postgresql.UUID(as_uuid=True)),
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
        sa.Column("source_authority", sa.String(16), nullable=False, server_default="central"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope = 'event_day' AND room_id IS NULL AND session_id IS NULL) OR "
            "(scope = 'room_day' AND room_id IS NOT NULL AND session_id IS NULL) OR "
            "(scope = 'session' AND room_id IS NULL AND session_id IS NOT NULL)",
            name="rotation_assignment_valid_scope",
        ),
        sa.CheckConstraint(
            "source_authority = 'central'", name="rotation_assignment_central_authority"
        ),
    )
    op.create_index(
        "ix_central_rotation_effective",
        "rotation_assignments",
        ["event_id", "event_day", "room_id", "session_id", "active"],
    )


def downgrade() -> None:
    op.drop_table("rotation_assignments")
