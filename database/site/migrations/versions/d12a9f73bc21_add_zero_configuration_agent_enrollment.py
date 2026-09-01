"""add zero-configuration Agent enrollment identity

Revision ID: d12a9f73bc21
Revises: c91e72f04a11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d12a9f73bc21"
down_revision = "c91e72f04a11"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("agent_identity", postgresql.UUID(as_uuid=True)))
    op.add_column("devices", sa.Column("machine_name", sa.String(255)))
    op.add_column(
        "devices",
        sa.Column("enrollment_state", sa.String(24), nullable=False, server_default="configured"),
    )
    op.create_unique_constraint(
        op.f("uq_site_devices_agent_identity"), "devices", ["agent_identity"]
    )


def downgrade():
    op.drop_constraint(op.f("uq_site_devices_agent_identity"), "devices", type_="unique")
    op.drop_column("devices", "enrollment_state")
    op.drop_column("devices", "machine_name")
    op.drop_column("devices", "agent_identity")
