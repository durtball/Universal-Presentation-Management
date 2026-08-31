"""add room Agent assignment/configuration and effective branding

Revision ID: c91e72f04a11
Revises: b82f7a19d340
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c91e72f04a11"
down_revision = "b82f7a19d340"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("event_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "devices",
        sa.Column("agent_role", sa.String(32), nullable=False, server_default="room_agent"),
    )
    op.add_column(
        "devices",
        sa.Column(
            "agent_configuration",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_foreign_key(
        op.f("fk_site_devices_event_id_events"),
        "devices",
        "events",
        ["event_id"],
        ["event_id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "event_branding",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="central_default"),
        sa.Column("local_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "configuration",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            ondelete="CASCADE",
            name=op.f("fk_site_event_branding_event_id_events"),
        ),
    )
    op.create_table(
        "event_branding_assets",
        sa.Column("event_branding_asset_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot", sa.String(64), nullable=False),
        sa.Column("media_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["event_branding.event_id"],
            ondelete="CASCADE",
            name=op.f("fk_site_event_branding_assets_event_id_event_branding"),
        ),
        sa.ForeignKeyConstraint(
            ["media_object_id"],
            ["media_objects.media_object_id"],
            ondelete="RESTRICT",
            name=op.f("fk_site_event_branding_assets_media_object_id_media_objects"),
        ),
        sa.UniqueConstraint(
            "event_id", "slot", name=op.f("uq_site_event_branding_assets_event_id")
        ),
    )
    op.create_table(
        "agent_change_feed",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(8), nullable=False),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("""
      CREATE FUNCTION upm_agent_change_feed() RETURNS trigger AS $$
      DECLARE row_record record; entity uuid;
      BEGIN
        IF TG_OP = 'DELETE' THEN row_record := OLD; ELSE row_record := NEW; END IF;
        entity := CASE TG_TABLE_NAME
          WHEN 'sessions' THEN row_record.session_id
          WHEN 'room_assignments' THEN row_record.room_assignment_id
          WHEN 'presentations' THEN row_record.presentation_id
          WHEN 'presentation_versions' THEN row_record.presentation_version_id
          WHEN 'presentation_assets' THEN row_record.presentation_asset_id
          WHEN 'rotation_assignments' THEN row_record.rotation_assignment_id
          WHEN 'event_branding' THEN row_record.event_id
          WHEN 'event_branding_assets' THEN row_record.event_branding_asset_id
          WHEN 'devices' THEN row_record.device_id END;
        INSERT INTO agent_change_feed(entity_type, entity_id, operation)
          VALUES (TG_TABLE_NAME, entity, lower(TG_OP));
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
      END; $$ LANGUAGE plpgsql;
    """)
    for table in (
        "sessions",
        "room_assignments",
        "presentations",
        "presentation_versions",
        "presentation_assets",
        "rotation_assignments",
        "event_branding",
        "event_branding_assets",
        "devices",
    ):
        op.execute(
            f"CREATE TRIGGER tr_{table}_agent_change AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION upm_agent_change_feed()"
        )


def downgrade():
    for table in (
        "sessions",
        "room_assignments",
        "presentations",
        "presentation_versions",
        "presentation_assets",
        "rotation_assignments",
        "event_branding",
        "event_branding_assets",
        "devices",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS tr_{table}_agent_change ON {table}")
    op.execute("DROP FUNCTION IF EXISTS upm_agent_change_feed")
    op.drop_table("agent_change_feed")
    op.drop_table("event_branding_assets")
    op.drop_table("event_branding")
    op.drop_constraint(op.f("fk_site_devices_event_id_events"), "devices", type_="foreignkey")
    op.drop_column("devices", "agent_configuration")
    op.drop_column("devices", "agent_role")
    op.drop_column("devices", "event_id")
