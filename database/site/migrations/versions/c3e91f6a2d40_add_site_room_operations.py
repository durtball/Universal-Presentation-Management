"""add Site room lifecycle, program mapping, and assignment constraints

Revision ID: c3e91f6a2d40
Revises: 92c5f1e7a3b4
Create Date: 2026-08-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e91f6a2d40"
down_revision: str | Sequence[str] | None = "92c5f1e7a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rooms",
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.alter_column("rooms", "enabled", server_default=None)
    op.add_column("rooms", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "program_room_mappings",
        sa.Column("program_room_mapping_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("imported_label", sa.String(length=255), nullable=False),
        sa.Column("normalized_imported_label", sa.String(length=255), nullable=False),
        sa.Column("room_id", sa.UUID(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            name=op.f("fk_site_program_room_mappings_event_id_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["rooms.room_id"],
            name=op.f("fk_site_program_room_mappings_room_id_rooms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["sites.site_id"],
            name=op.f("fk_site_program_room_mappings_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "program_room_mapping_id", name=op.f("pk_site_program_room_mappings")
        ),
        sa.UniqueConstraint(
            "event_id",
            "normalized_imported_label",
            name=op.f("uq_site_program_room_mappings_event_id"),
        ),
    )
    op.create_index(
        "ix_site_program_room_mappings_room",
        "program_room_mappings",
        ["room_id"],
        unique=False,
    )

    op.add_column(
        "room_assignments",
        sa.Column("program_room_mapping_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_site_room_assignments_program_room_mapping_id_program_room_mappings"),
        "room_assignments",
        "program_room_mappings",
        ["program_room_mapping_id"],
        ["program_room_mapping_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_site_room_assignments_active_session",
        "room_assignments",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index(
        "ix_site_room_assignments_active_room",
        "room_assignments",
        ["room_id", "active"],
        unique=False,
    )
    op.create_index(
        "uq_site_device_assignments_active_room_role",
        "device_assignments",
        ["room_id", "role"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index(
        "uq_site_device_assignments_active_device",
        "device_assignments",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    op.drop_index("uq_site_device_assignments_active_device", table_name="device_assignments")
    op.drop_index("uq_site_device_assignments_active_room_role", table_name="device_assignments")
    op.drop_index("ix_site_room_assignments_active_room", table_name="room_assignments")
    op.drop_index("uq_site_room_assignments_active_session", table_name="room_assignments")
    op.drop_constraint(
        op.f("fk_site_room_assignments_program_room_mapping_id_program_room_mappings"),
        "room_assignments",
        type_="foreignkey",
    )
    op.drop_column("room_assignments", "program_room_mapping_id")
    op.drop_index("ix_site_program_room_mappings_room", table_name="program_room_mappings")
    op.drop_table("program_room_mappings")
    op.drop_column("rooms", "archived_at")
    op.drop_column("rooms", "enabled")
