"""add Central event deployments

Revision ID: e3b7a91c4d20
Revises: c91a4b2d8f20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3b7a91c4d20"
down_revision: str | Sequence[str] | None = "c91a4b2d8f20"
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
            "event_id",
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
        sa.Column("acknowledged_revision", sa.Integer(), nullable=False),
        sa.Column("deployment_requested_at", sa.DateTime(timezone=True)),
        sa.Column("last_synchronization_at", sa.DateTime(timezone=True)),
        sa.Column("successfully_deployed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.String(2048)),
        sa.Column("site_status", sa.String(32)),
        sa.Column(
            "summary_counts",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("desired_revision >= 0", name="desired_revision_nonnegative"),
        sa.CheckConstraint("acknowledged_revision >= 0", name="acknowledged_revision_nonnegative"),
        sa.CheckConstraint(
            "acknowledged_revision <= desired_revision", name="acknowledged_not_ahead"
        ),
        sa.UniqueConstraint(
            "event_id", "site_id", name=op.f("uq_central_event_deployments_event_id")
        ),
    )
    op.create_index(
        "ix_central_event_deployments_site_status", "event_deployments", ["site_id", "status"]
    )
    op.create_index(
        "ix_central_event_deployments_event_status",
        "event_deployments",
        ["event_id", "status"],
    )
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "deployment_revision >= 1",
            name=op.f("ck_central_event_deployment_revisions_revision_positive"),
        ),
        sa.UniqueConstraint(
            "deployment_id",
            "deployment_revision",
            name=op.f("uq_central_event_deployment_revisions_deployment_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("event_deployment_revisions")
    op.drop_index("ix_central_event_deployments_event_status", table_name="event_deployments")
    op.drop_index("ix_central_event_deployments_site_status", table_name="event_deployments")
    op.drop_table("event_deployments")
