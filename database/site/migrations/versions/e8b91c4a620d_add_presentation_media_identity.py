"""add presentation identity and canonical media labels

Revision ID: e8b91c4a620d
Revises: c3e91f6a2d40
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b91c4a620d"
down_revision: str | Sequence[str] | None = "c3e91f6a2d40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presentations", sa.Column("presentation_identifier", sa.String(128)))
    op.add_column("presentations", sa.Column("presentation_identifier_source", sa.String(16)))
    op.add_column("presentations", sa.Column("external_presentation_id", sa.String(512)))
    op.execute(
        """UPDATE presentations SET
        presentation_identifier = COALESCE(NULLIF(BTRIM(presentation_code), ''),
          'UPM-SITE-' || UPPER(RIGHT(REPLACE(presentation_id::text, '-', ''), 12))),
        presentation_identifier_source = CASE WHEN NULLIF(BTRIM(presentation_code), '') IS NULL
          THEN 'generated' ELSE 'imported' END,
        external_presentation_id = NULLIF(BTRIM(presentation_code), '')"""
    )
    op.alter_column("presentations", "presentation_identifier", nullable=False)
    op.alter_column("presentations", "presentation_identifier_source", nullable=False)
    op.create_check_constraint(
        "presentationidentifiersource",
        "presentations",
        "presentation_identifier_source IN ('imported', 'generated')",
    )
    op.create_index(
        "uq_site_presentations_event_identifier",
        "presentations",
        ["event_id", "presentation_identifier"],
        unique=True,
    )
    op.create_index(
        "ix_site_presentations_external_identifier",
        "presentations",
        ["external_presentation_id"],
    )
    op.add_column("media_objects", sa.Column("canonical_filename", sa.String(1024)))


def downgrade() -> None:
    op.drop_column("media_objects", "canonical_filename")
    op.drop_index("ix_site_presentations_external_identifier", table_name="presentations")
    op.drop_index("uq_site_presentations_event_identifier", table_name="presentations")
    op.drop_constraint("presentationidentifiersource", "presentations", type_="check")
    op.drop_column("presentations", "external_presentation_id")
    op.drop_column("presentations", "presentation_identifier_source")
    op.drop_column("presentations", "presentation_identifier")
