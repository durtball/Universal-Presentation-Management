"""Add operator-authoritative presentation media matching state.

Revision ID: c7a91e4b2d60
Revises: b93e4a71d520
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a91e4b2d60"
down_revision: str | Sequence[str] | None = "b93e4a71d520"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("mediamatchstate", "presentation_media_imports", type_="check")
    op.create_check_constraint(
        "mediamatchstate",
        "presentation_media_imports",
        "match_state IN ('suggested','confirmed','exact','high_confidence',"
        "'ambiguous','unmatched','manual')",
    )
    op.add_column("presentation_media_imports", sa.Column("confirmed_by", sa.String(255)))
    op.add_column(
        "presentation_media_imports", sa.Column("confirmed_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.execute(
        "UPDATE presentation_media_imports SET match_state = CASE "
        "WHEN match_state = 'confirmed' THEN 'manual' "
        "WHEN match_state = 'suggested' THEN 'high_confidence' ELSE match_state END"
    )
    op.drop_column("presentation_media_imports", "confirmed_at")
    op.drop_column("presentation_media_imports", "confirmed_by")
    op.drop_constraint("mediamatchstate", "presentation_media_imports", type_="check")
    op.create_check_constraint(
        "mediamatchstate",
        "presentation_media_imports",
        "match_state IN ('exact','high_confidence','ambiguous','unmatched','manual')",
    )
