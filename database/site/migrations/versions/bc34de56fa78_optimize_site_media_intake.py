"""optimize bounded Site presentation-media intake

Revision ID: bc34de56fa78
Revises: aa12bc34de56
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bc34de56fa78"
down_revision: str | Sequence[str] | None = "aa12bc34de56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_site_media_event_intake",
        "media_objects",
        ["event_id", "deleted_at", "created_at"],
    )
    op.create_index(
        "ix_site_presentation_assets_media",
        "presentation_assets",
        ["media_object_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_site_presentation_assets_media", table_name="presentation_assets")
    op.drop_index("ix_site_media_event_intake", table_name="media_objects")
