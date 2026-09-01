"""repair Agent change feed and track device assignments

Revision ID: a73c5e91f204
Revises: d12a9f73bc21
"""

from alembic import op

revision = "a73c5e91f204"
down_revision = "d12a9f73bc21"
branch_labels = None
depends_on = None


TRACKED_ENTITIES = {
    "sessions": "session_id",
    "room_assignments": "room_assignment_id",
    "presentations": "presentation_id",
    "presentation_versions": "presentation_version_id",
    "presentation_assets": "presentation_asset_id",
    "rotation_assignments": "rotation_assignment_id",
    "event_branding": "event_id",
    "event_branding_assets": "event_branding_asset_id",
    "devices": "device_id",
    "device_assignments": "device_assignment_id",
}


def _install_function(include_device_assignments: bool) -> None:
    entities = dict(TRACKED_ENTITIES)
    if not include_device_assignments:
        entities.pop("device_assignments")
    cases = "\n".join(
        f"          WHEN '{table}' THEN '{column}'" for table, column in entities.items()
    )
    op.execute(f"""
      CREATE OR REPLACE FUNCTION upm_agent_change_feed() RETURNS trigger AS $$
      DECLARE payload jsonb; identity_column text; entity uuid;
      BEGIN
        payload := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
        identity_column := CASE TG_TABLE_NAME
{cases}
          ELSE NULL
        END;
        IF identity_column IS NULL THEN
          RAISE EXCEPTION 'unsupported Agent change-feed table: %', TG_TABLE_NAME;
        END IF;
        entity := (payload ->> identity_column)::uuid;
        INSERT INTO agent_change_feed(entity_type, entity_id, operation)
          VALUES (TG_TABLE_NAME, entity, lower(TG_OP));
        IF TG_OP = 'DELETE' THEN
          RETURN OLD;
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
    """)


def upgrade():
    _install_function(include_device_assignments=True)
    op.execute(
        "DROP TRIGGER IF EXISTS tr_device_assignments_agent_change ON device_assignments"
    )
    op.execute(
        "CREATE TRIGGER tr_device_assignments_agent_change "
        "AFTER INSERT OR UPDATE OR DELETE ON device_assignments "
        "FOR EACH ROW EXECUTE FUNCTION upm_agent_change_feed()"
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS tr_device_assignments_agent_change ON device_assignments"
    )
    _install_function(include_device_assignments=False)
