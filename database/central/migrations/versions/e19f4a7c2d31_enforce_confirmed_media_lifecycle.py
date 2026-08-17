"""Enforce the canonical confirmed-media lifecycle.

Revision ID: e19f4a7c2d31
Revises: c7a91e4b2d60
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e19f4a7c2d31"
down_revision: str | Sequence[str] | None = "c7a91e4b2d60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Repair the historical contradictory label without changing version identity.
    op.execute(
        "UPDATE presentation_media_imports SET import_state = 'assigned' "
        "WHERE match_state = 'confirmed' AND import_state = 'needs_review' "
        "AND presentation_id IS NOT NULL AND presentation_version_id IS NOT NULL"
    )
    op.create_check_constraint(
        "confirmed_media_has_canonical_version",
        "presentation_media_imports",
        "match_state <> 'confirmed' OR "
        "(presentation_id IS NOT NULL AND presentation_version_id IS NOT NULL "
        "AND import_state <> 'needs_review')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "confirmed_media_has_canonical_version",
        "presentation_media_imports",
        type_="check",
    )
