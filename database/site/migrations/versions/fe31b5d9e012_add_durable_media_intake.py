"""Add durable media Intake disposition and supplemental asset roles."""

import sqlalchemy as sa
from alembic import op

revision = "fe31b5d9e012"
down_revision = ("fa12e37bd908", "a7d31c9e5b42")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_objects",
        sa.Column("disposition", sa.String(16), nullable=False, server_default="intake"),
    )
    op.add_column("media_objects", sa.Column("rejected_by", sa.String(255)))
    op.add_column("media_objects", sa.Column("rejected_at", sa.DateTime(timezone=True)))
    op.add_column("media_objects", sa.Column("rejection_reason", sa.String(2048)))
    op.create_check_constraint(
        "media_object_disposition",
        "media_objects",
        "disposition IN ('intake','authoritative','rejected')",
    )
    op.drop_constraint("assetkind", "presentation_assets", type_="check")
    op.create_check_constraint(
        "assetkind",
        "presentation_assets",
        "kind IN ('original','derivative','image','video','document','other')",
    )
    op.drop_constraint(
        "ck_site_presentation_assets_derivative_source", "presentation_assets", type_="check"
    )
    op.create_check_constraint(
        "ck_site_presentation_assets_derivative_source",
        "presentation_assets",
        "(kind = 'derivative' AND source_asset_id IS NOT NULL) OR "
        "(kind <> 'derivative' AND source_asset_id IS NULL)",
    )


def downgrade() -> None:
    raise RuntimeError("durable Intake disposition is not safely reversible")
