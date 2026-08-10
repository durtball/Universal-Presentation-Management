"""add permanent event program and import domain

Revision ID: 7b2d9a4e6c11
Revises: e3b7a91c4d20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from uuid6 import uuid7

revision: str = "7b2d9a4e6c11"
down_revision: str | Sequence[str] | None = "e3b7a91c4d20"
branch_labels = None
depends_on = None


def enum(name: str, *values: str, length: int) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=length)


def record_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    for name, length in [
        ("given_name", 255),
        ("middle_name", 255),
        ("family_name", 255),
        ("prefix", 64),
        ("suffix", 64),
        ("preferred_name", 255),
        ("normalized_name", 512),
        ("normalized_email", 320),
        ("phone", 64),
        ("professional_title", 255),
    ]:
        op.add_column("persons", sa.Column(name, sa.String(length)))
    op.add_column(
        "persons", sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False)
    )
    op.execute("UPDATE persons SET normalized_name = lower(trim(display_name))")
    op.alter_column("persons", "normalized_name", nullable=False)
    op.create_index("ix_central_persons_normalized_email", "persons", ["normalized_email"])

    op.add_column("events", sa.Column("description", sa.Text()))
    op.add_column(
        "events", sa.Column("timezone", sa.String(100), server_default="UTC", nullable=False)
    )

    participant_status = enum(
        "participantstatus", "pending", "active", "inactive", "cancelled", length=16
    )
    for name, kind in [
        ("display_name", sa.String(255)),
        ("professional_title", sa.String(255)),
        ("organization", sa.String(255)),
        ("registration_status", sa.String(100)),
        ("notes", sa.Text()),
        ("source", sa.String(255)),
    ]:
        op.add_column("event_participations", sa.Column(name, kind))
    op.add_column(
        "event_participations",
        sa.Column(
            "participant_status", participant_status, server_default="active", nullable=False
        ),
    )
    op.add_column(
        "event_participations",
        sa.Column("is_presenter", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "event_participations",
        sa.Column(
            "source_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    for name, kind in [
        ("subtitle", sa.String(255)),
        ("description", sa.Text()),
        ("session_code", sa.String(255)),
        ("session_type", sa.String(100)),
        ("location_name", sa.String(255)),
        ("source", sa.String(255)),
    ]:
        op.add_column("sessions", sa.Column(name, kind))
    op.add_column(
        "sessions",
        sa.Column(
            "location_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
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
    op.add_column(
        "sessions", sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "sessions",
        sa.Column(
            "source_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "schedule_order",
        "sessions",
        "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
    )
    op.create_index("ix_central_sessions_event_schedule", "sessions", ["event_id", "starts_at"])
    op.create_unique_constraint(
        "uq_central_sessions_event_id", "sessions", ["event_id", "session_code"]
    )

    op.add_column(
        "session_participants",
        sa.Column("presenter_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "session_participants",
        sa.Column("primary_presenter", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("session_participants", sa.Column("external_relationship_id", sa.String(512)))
    op.add_column("session_participants", sa.Column("source", sa.String(255)))
    op.add_column("session_participants", sa.Column("notes", sa.Text()))

    for name, kind in [
        ("description", sa.Text()),
        ("presentation_code", sa.String(255)),
        ("scheduled_at", sa.DateTime(timezone=True)),
        ("source", sa.String(255)),
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
        "uq_central_presentations_event_id",
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
        "presentations",
        sa.Column(
            "source_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    now = sa.text("CURRENT_TIMESTAMP")
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
        sa.Column("source", sa.String(255)),
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
                "session_id,association_type,sort_order,primary_session,created_at,updated_at,"
                "revision) VALUES (:id,:presentation,:session,'scheduled',0,true,"
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
    op.add_column("presentation_presenters", sa.Column("source", sa.String(255)))
    op.add_column(
        "presentation_presenters",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.add_column(
        "presentation_presenters",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.add_column(
        "presentation_presenters",
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    )
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
        "pk_central_presentation_presenters", "presentation_presenters", type_="primary"
    )
    old_presenter_fk = next(
        key["name"]
        for key in sa.inspect(connection).get_foreign_keys("presentation_presenters")
        if key["constrained_columns"] == ["session_participant_id"]
    )
    op.drop_constraint(old_presenter_fk, "presentation_presenters", type_="foreignkey")
    op.drop_column("presentation_presenters", "session_participant_id")
    op.create_primary_key(
        "pk_central_presentation_presenters",
        "presentation_presenters",
        ["presentation_presenter_id"],
    )
    op.create_foreign_key(
        "fk_central_presentation_presenters_event_participation_id",
        "presentation_presenters",
        "event_participations",
        ["event_participation_id"],
        ["event_participation_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_central_presentation_presenters_presentation_id",
        "presentation_presenters",
        ["presentation_id", "event_participation_id", "role"],
    )

    op.create_table(
        "external_identifiers",
        sa.Column("external_identifier_id", sa.UUID(), primary_key=True),
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
        sa.Column("normalized_external_id", sa.String(512), nullable=False),
        sa.Column(
            "scope", enum("externalidentifierscope", "global", "event", length=16), nullable=False
        ),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("event_id", sa.UUID(), sa.ForeignKey("events.event_id", ondelete="RESTRICT")),
        sa.Column("source", sa.String(255)),
        *record_columns(),
        sa.UniqueConstraint("namespace", "normalized_external_id", "scope_key"),
        sa.UniqueConstraint("entity_type", "entity_id", "namespace", "scope_key"),
    )
    op.create_index(
        "ix_central_external_identifier_entity",
        "external_identifiers",
        ["entity_type", "entity_id"],
    )

    op.create_table(
        "import_sources",
        sa.Column("import_source_id", sa.UUID(), primary_key=True),
        sa.Column("filename", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("source_type", enum("importsourcetype", "csv", "xlsx", length=8), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "import_batches",
        sa.Column("import_batch_id", sa.UUID(), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "import_source_id",
            sa.UUID(),
            sa.ForeignKey("import_sources.import_source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(1024), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("importer_type", sa.String(100), nullable=False),
        sa.Column(
            "status",
            enum(
                "importstatus",
                "uploaded",
                "parsing",
                "staged",
                "review",
                "ready",
                "committing",
                "committed",
                "failed",
                "cancelled",
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("valid_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("committed_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("failure_summary", sa.Text()),
        sa.Column("reviewed_domain_revision", sa.Integer()),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        *record_columns(),
        sa.UniqueConstraint("event_id", "source_sha256", "importer_type"),
    )
    op.create_index(
        "ix_central_import_batches_event_status", "import_batches", ["event_id", "status"]
    )
    op.create_table(
        "import_rows",
        sa.Column("import_row_id", sa.UUID(), primary_key=True),
        sa.Column(
            "import_batch_id",
            sa.UUID(),
            sa.ForeignKey("import_batches.import_batch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("raw_values", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_values", postgresql.JSONB(), nullable=False),
        sa.Column("corrected_values", postgresql.JSONB()),
        sa.Column(
            "entity_type",
            enum(
                "importentitytype",
                "person",
                "participant",
                "session",
                "presentation",
                "relationship",
                "unknown",
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "validation_state",
            enum("importvalidationstate", "pending", "valid", "warning", "error", length=16),
            nullable=False,
        ),
        sa.Column(
            "match_outcome",
            enum(
                "identitymatchoutcome",
                "exact",
                "strong_candidate",
                "no_match",
                "ambiguous",
                "conflict",
                length=24,
            ),
        ),
        sa.Column(
            "proposed_person_id", sa.UUID(), sa.ForeignKey("persons.person_id", ondelete="RESTRICT")
        ),
        sa.Column("candidate_person_ids", postgresql.JSONB(), nullable=False),
        sa.Column("match_confidence", sa.Numeric(5, 4)),
        sa.Column("match_reason", sa.Text()),
        sa.Column(
            "proposed_action",
            enum(
                "importproposedaction",
                "match_existing",
                "create_new",
                "create_or_update",
                "link",
                "ignore",
                "reject",
                length=24,
            ),
        ),
        sa.Column("conflict_state", sa.String(64)),
        sa.Column(
            "resolution_action",
            enum(
                "reconciliationaction",
                "accept_match",
                "choose_person",
                "create_person",
                "correct_values",
                "ignore",
                "reject",
                length=24,
            ),
        ),
        sa.Column(
            "resolved_person_id", sa.UUID(), sa.ForeignKey("persons.person_id", ondelete="RESTRICT")
        ),
        sa.Column("resolved_by", sa.String(255)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("committed_entity_ids", postgresql.JSONB(), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        *record_columns(),
        sa.UniqueConstraint("import_batch_id", "source_row_number"),
    )
    op.create_index(
        "ix_central_import_rows_batch_state", "import_rows", ["import_batch_id", "validation_state"]
    )
    op.create_table(
        "import_validation_issues",
        sa.Column("import_validation_issue_id", sa.UUID(), primary_key=True),
        sa.Column(
            "import_row_id",
            sa.UUID(),
            sa.ForeignKey("import_rows.import_row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "severity", enum("validationseverity", "warning", "error", length=8), nullable=False
        ),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("field_name", sa.String(255)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_central_import_issues_row_severity",
        "import_validation_issues",
        ["import_row_id", "severity"],
    )
    op.create_table(
        "reconciliation_decisions",
        sa.Column("reconciliation_decision_id", sa.UUID(), primary_key=True),
        sa.Column(
            "import_row_id",
            sa.UUID(),
            sa.ForeignKey("import_rows.import_row_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "action",
            enum(
                "reconciliationaction",
                "accept_match",
                "choose_person",
                "create_person",
                "correct_values",
                "ignore",
                "reject",
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "selected_person_id", sa.UUID(), sa.ForeignKey("persons.person_id", ondelete="RESTRICT")
        ),
        sa.Column("corrected_values", postgresql.JSONB()),
        sa.Column("decided_by", sa.String(255), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text()),
    )


def downgrade() -> None:
    op.drop_constraint("schedule_order", "sessions", type_="check")
    op.drop_index("ix_central_persons_normalized_email", table_name="persons")
    op.drop_table("reconciliation_decisions")
    op.drop_index("ix_central_import_issues_row_severity", table_name="import_validation_issues")
    op.drop_table("import_validation_issues")
    op.drop_index("ix_central_import_rows_batch_state", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_central_import_batches_event_status", table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_table("import_sources")
    op.drop_index("ix_central_external_identifier_entity", table_name="external_identifiers")
    op.drop_table("external_identifiers")
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
            "source_metadata",
            "processing_status",
            "workflow_status",
            "source",
            "scheduled_at",
            "presentation_code",
            "description",
        ],
        "session_participants": [
            "notes",
            "source",
            "external_relationship_id",
            "primary_presenter",
            "presenter_order",
        ],
        "sessions": [
            "source_metadata",
            "sort_order",
            "status",
            "location_metadata",
            "source",
            "location_name",
            "session_type",
            "session_code",
            "description",
            "subtitle",
        ],
        "event_participations": [
            "source_metadata",
            "is_presenter",
            "participant_status",
            "source",
            "notes",
            "registration_status",
            "organization",
            "professional_title",
            "display_name",
        ],
        "events": ["timezone", "description"],
        "persons": [
            "active",
            "professional_title",
            "phone",
            "normalized_email",
            "normalized_name",
            "preferred_name",
            "suffix",
            "prefix",
            "family_name",
            "middle_name",
            "given_name",
        ],
    }.items():
        for column in columns:
            op.drop_column(table, column)
    op.drop_index("ix_central_sessions_event_schedule", table_name="sessions")
