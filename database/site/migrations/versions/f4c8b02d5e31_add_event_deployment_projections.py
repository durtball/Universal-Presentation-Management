"""add Site event deployment projections

Revision ID: f4c8b02d5e31
Revises: d04f87a2c311
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4c8b02d5e31"
down_revision: str | Sequence[str] | None = "d04f87a2c311"
branch_labels = None
depends_on = None


def deployment_status() -> sa.Enum:
    return sa.Enum(
        "draft",
        "pending",
        "deploying",
        "deployed",
        "update_pending",
        "failed",
        "revoked",
        "archived",
        name="eventdeploymentstatus",
        native_enum=False,
        create_constraint=True,
        length=24,
    )


def upgrade() -> None:
    op.create_table(
        "event_deployments",
        sa.Column("deployment_id", sa.UUID(), primary_key=True),
        sa.Column(
            "central_event_id",
            sa.UUID(),
            sa.ForeignKey("events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            sa.UUID(),
            sa.ForeignKey("sites.site_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", deployment_status(), nullable=False),
        sa.Column("desired_revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer(), nullable=False),
        sa.Column("last_central_synchronization_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("failure_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.String(2048)),
        sa.Column(
            "current_snapshot",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "summary_counts",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("desired_revision >= 1", name="desired_revision_positive"),
        sa.CheckConstraint("applied_revision >= 0", name="applied_revision_nonnegative"),
        sa.CheckConstraint("applied_revision <= desired_revision", name="applied_not_ahead"),
    )
    op.create_index("ix_site_event_deployments_status", "event_deployments", ["status"])
    op.create_table(
        "event_deployment_revisions",
        sa.Column("deployment_revision_id", sa.UUID(), primary_key=True),
        sa.Column(
            "deployment_id",
            sa.UUID(),
            sa.ForeignKey("event_deployments.deployment_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("deployment_revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("deployment_revision >= 1", name="deployment_revision_positive"),
        sa.UniqueConstraint(
            "deployment_id",
            "deployment_revision",
            name=op.f("uq_site_event_deployment_revisions_deployment_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("event_deployment_revisions")
    op.drop_index("ix_site_event_deployments_status", table_name="event_deployments")
    op.drop_table("event_deployments")
