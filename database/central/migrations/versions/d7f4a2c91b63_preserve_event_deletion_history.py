"""preserve event audit and transport history across permanent deletion

Revision ID: d7f4a2c91b63
Revises: ca91e2b7f430
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d7f4a2c91b63"
down_revision: str | Sequence[str] | None = "ca91e2b7f430"
branch_labels = None
depends_on = None


AUDIT_FK = "fk_central_audit_records_event_id_events"
OUTBOX_FK = "fk_central_outbox_events_event_id_events"
SYNC_FK = "fk_central_sync_events_event_id_events"


def upgrade() -> None:
    # Audit event_id is historical identity rather than a relationship to a live
    # aggregate.  Keeping the UUID makes old records useful after hard deletion.
    op.drop_constraint(AUDIT_FK, "audit_records", type_="foreignkey")
    for table, constraint in (("outbox_events", OUTBOX_FK), ("sync_events", SYNC_FK)):
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint,
            table,
            "events",
            ["event_id"],
            ["event_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Transport values have already detached safely. Historical audit UUIDs
    # must not be destroyed merely to make a rollback possible, so restore the
    # old constraint NOT VALID: PostgreSQL enforces it for subsequent writes
    # without rejecting retained evidence created while this revision was live.
    for table, constraint in (("sync_events", SYNC_FK), ("outbox_events", OUTBOX_FK)):
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint,
            table,
            "events",
            ["event_id"],
            ["event_id"],
            ondelete="RESTRICT",
        )
    op.execute(
        f"ALTER TABLE audit_records ADD CONSTRAINT {AUDIT_FK} "
        "FOREIGN KEY (event_id) REFERENCES events (event_id) "
        "ON DELETE RESTRICT NOT VALID"
    )
