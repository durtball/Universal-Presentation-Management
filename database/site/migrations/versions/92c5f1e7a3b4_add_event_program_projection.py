"""add Site event program projection

Revision ID: 92c5f1e7a3b4
Revises: f4c8b02d5e31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from uuid6 import uuid7

revision: str = "92c5f1e7a3b4"
down_revision: str | Sequence[str] | None = "f4c8b02d5e31"
branch_labels = None
depends_on = None


def enum(name: str, *values: str, length: int) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=length)


def record_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    ]


def upgrade() -> None:
    op.add_column("person_projections", sa.Column("given_name", sa.String(255)))
    op.add_column("person_projections", sa.Column("family_name", sa.String(255)))
    op.add_column("events", sa.Column("description", sa.Text()))
    op.add_column(
        "events", sa.Column("timezone", sa.String(100), server_default="UTC", nullable=False)
    )

    op.add_column("event_participations", sa.Column("display_name", sa.String(255)))
    op.add_column("event_participations", sa.Column("professional_title", sa.String(255)))
    op.add_column("event_participations", sa.Column("organization", sa.String(255)))
    op.add_column(
        "event_participations",
        sa.Column(
            "participant_status",
            enum("participantstatus", "pending", "active", "inactive", "cancelled", length=16),
            server_default="active",
            nullable=False,
        ),
    )
    op.add_column(
        "event_participations",
        sa.Column("is_presenter", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "event_participations",
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )

    for name, kind in [
        ("subtitle", sa.String(255)),
        ("description", sa.Text()),
        ("session_code", sa.String(255)),
        ("session_type", sa.String(100)),
        ("location_name", sa.String(255)),
    ]:
        op.add_column("sessions", sa.Column(name, kind))
    op.add_column(
        "sessions",
        sa.Column(
            "status",
            enum(
                "sessionstatus",
                "draft",
                "scheduled",
                "in_progress",
                "completed",
                "cancelled",
                "archived",
                length=16,
            ),
            server_default="draft",
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_site_sessions_event_code", "sessions", ["event_id", "session_code"]
    )
    op.add_column(
        "sessions", sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "sessions", sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False)
    )

    op.add_column(
        "session_participants",
        sa.Column("presenter_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "session_participants",
        sa.Column("primary_presenter", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "session_participants",
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )

    for name, kind in [
        ("description", sa.Text()),
        ("presentation_code", sa.String(255)),
        ("scheduled_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("presentations", sa.Column(name, kind))
    op.add_column(
        "presentations",
        sa.Column(
            "workflow_status",
            enum(
                "presentationworkflowstatus",
                "expected",
                "received",
                "updated",
                "needs_validation",
                "approved",
                "ready",
                "deployed",
                "problem",
                "archived",
                "cancelled",
                length=24,
            ),
            server_default="expected",
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_site_presentations_event_code",
        "presentations",
        ["event_id", "presentation_code"],
    )
    op.add_column(
        "presentations",
        sa.Column(
            "processing_status",
            enum(
                "presentationprocessingstatus",
                "not_started",
                "queued",
                "processing",
                "succeeded",
                "failed",
                length=16,
            ),
            server_default="not_started",
            nullable=False,
        ),
    )
    op.add_column(
        "presentations", sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False)
    )

    op.create_table(
        "presentation_sessions",
        sa.Column("presentation_session_id", sa.UUID(), primary_key=True),
        sa.Column(
            "presentation_id",
            sa.UUID(),
            sa.ForeignKey("presentations.presentation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.UUID(),
            sa.ForeignKey("sessions.session_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("association_type", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("primary_session", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *record_columns(),
        sa.UniqueConstraint("presentation_id", "session_id"),
    )
    connection = op.get_bind()
    for presentation_id, session_id in connection.execute(
        sa.text(
            "SELECT presentation_id, session_id FROM presentations WHERE session_id IS NOT NULL"
        )
    ):
        connection.execute(
            sa.text(
                "INSERT INTO presentation_sessions (presentation_session_id,presentation_id,"
                "session_id,association_type,sort_order,primary_session,active,created_at,"
                "updated_at,revision) VALUES (:id,:presentation,:session,'scheduled',0,true,true,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1)"
            ),
            {"id": uuid7(), "presentation": presentation_id, "session": session_id},
        )

    op.add_column("presentation_presenters", sa.Column("presentation_presenter_id", sa.UUID()))
    op.add_column("presentation_presenters", sa.Column("event_participation_id", sa.UUID()))
    op.add_column(
        "presentation_presenters",
        sa.Column("role", sa.String(64), server_default="presenter", nullable=False),
    )
    op.add_column(
        "presentation_presenters",
        sa.Column("presenter_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "presentation_presenters",
        sa.Column("primary_presenter", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "presentation_presenters",
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    for column in record_columns():
        op.add_column("presentation_presenters", column)
    op.execute(
        sa.text(
            "UPDATE presentation_presenters pp SET event_participation_id = "
            "sp.event_participation_id FROM session_participants sp WHERE "
            "sp.session_participant_id = pp.session_participant_id"
        )
    )
    for presentation_id, participant_id in connection.execute(
        sa.text("SELECT presentation_id,event_participation_id FROM presentation_presenters")
    ):
        connection.execute(
            sa.text(
                "UPDATE presentation_presenters SET presentation_presenter_id=:id "
                "WHERE presentation_id=:presentation AND event_participation_id=:participant"
            ),
            {"id": uuid7(), "presentation": presentation_id, "participant": participant_id},
        )
    op.alter_column("presentation_presenters", "presentation_presenter_id", nullable=False)
    op.alter_column("presentation_presenters", "event_participation_id", nullable=False)
    op.drop_constraint(
        "pk_site_presentation_presenters", "presentation_presenters", type_="primary"
    )
    old_presenter_fk = next(
        key["name"]
        for key in sa.inspect(connection).get_foreign_keys("presentation_presenters")
        if key["constrained_columns"] == ["session_participant_id"]
    )
    op.drop_constraint(old_presenter_fk, "presentation_presenters", type_="foreignkey")
    op.drop_column("presentation_presenters", "session_participant_id")
    op.create_primary_key(
        "pk_site_presentation_presenters", "presentation_presenters", ["presentation_presenter_id"]
    )
    op.create_foreign_key(
        "fk_site_presentation_presenters_event_participation_id",
        "presentation_presenters",
        "event_participations",
        ["event_participation_id"],
        ["event_participation_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_site_presentation_presenters_assignment",
        "presentation_presenters",
        ["presentation_id", "event_participation_id", "role"],
    )

    op.create_table(
        "external_identifier_projections",
        sa.Column("external_identifier_id", sa.UUID(), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "entity_type",
            enum(
                "externalentitytype",
                "person",
                "event_participation",
                "session",
                "session_presenter",
                "presentation",
                "presentation_session",
                "presentation_presenter",
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(255), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *record_columns(),
        sa.UniqueConstraint("namespace", "external_id", "event_id"),
    )
    op.create_index(
        "ix_site_external_identifier_entity",
        "external_identifier_projections",
        ["entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_site_external_identifier_entity", table_name="external_identifier_projections"
    )
    op.drop_table("external_identifier_projections")
    op.drop_table("presentation_presenters")
    op.create_table(
        "presentation_presenters",
        sa.Column(
            "presentation_id",
            sa.UUID(),
            sa.ForeignKey("presentations.presentation_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "session_participant_id",
            sa.UUID(),
            sa.ForeignKey("session_participants.session_participant_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.drop_table("presentation_sessions")
    for table, columns in {
        "presentations": [
            "active",
            "processing_status",
            "workflow_status",
            "scheduled_at",
            "presentation_code",
            "description",
        ],
        "session_participants": ["active", "primary_presenter", "presenter_order"],
        "sessions": [
            "active",
            "sort_order",
            "status",
            "location_name",
            "session_type",
            "session_code",
            "description",
            "subtitle",
        ],
        "event_participations": [
            "active",
            "is_presenter",
            "participant_status",
            "organization",
            "professional_title",
            "display_name",
        ],
        "events": ["timezone", "description"],
        "person_projections": ["family_name", "given_name"],
    }.items():
        for column in columns:
            op.drop_column(table, column)
