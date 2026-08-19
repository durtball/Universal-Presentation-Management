"""Add durable media Intake disposition and supplemental asset roles."""

import sqlalchemy as sa
from alembic import op

revision = "fe21a4c8d901"
down_revision = ("bf73a10c2e44", "c4f8a2d91e60")
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("intake_storage_root_id", "rejected_storage_root_id"):
        op.add_column("presentation_media_imports", sa.Column(name, sa.UUID()))
        op.create_foreign_key(
            f"fk_media_import_{name}",
            "presentation_media_imports",
            "storage_roots",
            [name],
            ["storage_root_id"],
            ondelete="RESTRICT",
        )
    for name in ("intake_storage_key", "rejected_storage_key", "rejection_reason"):
        op.add_column("presentation_media_imports", sa.Column(name, sa.String(2048)))
    op.add_column("presentation_media_imports", sa.Column("rejected_by", sa.String(255)))
    op.add_column(
        "presentation_media_imports", sa.Column("rejected_at", sa.DateTime(timezone=True))
    )
    op.drop_constraint("mediaimportstate", "presentation_media_imports", type_="check")
    op.create_check_constraint(
        "mediaimportstate",
        "presentation_media_imports",
        "import_state IN ('uploading','staged','needs_review','assigned','transfer_queued',"
        "'transferring','site_ready','retry_wait','failed','cancelled','rejected')",
    )
    op.drop_constraint("assetkind", "presentation_assets", type_="check")
    op.create_check_constraint(
        "assetkind",
        "presentation_assets",
        "kind IN ('original','derivative','image','video','document','other')",
    )
    op.drop_constraint(
        "ck_central_presentation_assets_derivative_source", "presentation_assets", type_="check"
    )
    op.create_check_constraint(
        "ck_central_presentation_assets_derivative_source",
        "presentation_assets",
        "(kind = 'derivative' AND source_asset_id IS NOT NULL) OR "
        "(kind <> 'derivative' AND source_asset_id IS NULL)",
    )


def downgrade() -> None:
    raise RuntimeError("durable Intake disposition is not safely reversible")
