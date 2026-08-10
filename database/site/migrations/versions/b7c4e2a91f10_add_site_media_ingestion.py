"""add Site media ingestion metadata and availability state

Revision ID: b7c4e2a91f10
Revises: 6ed903da8ed5
Create Date: 2026-08-10 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c4e2a91f10"
down_revision: str | Sequence[str] | None = "6ed903da8ed5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "storage_targets",
        sa.Column(
            "safety_reserve_bytes",
            sa.BigInteger(),
            server_default="1073741824",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_site_storage_targets_safety_reserve_nonnegative"),
        "storage_targets",
        "safety_reserve_bytes >= 0",
    )
    op.alter_column("storage_targets", "safety_reserve_bytes", server_default=None)

    op.add_column(
        "media_objects",
        sa.Column(
            "original_filename", sa.String(length=1024), server_default="unknown", nullable=False
        ),
    )
    op.add_column("media_objects", sa.Column("hash_algorithm", sa.String(length=32)))
    op.add_column(
        "media_objects",
        sa.Column(
            "availability",
            sa.Enum(
                "staging",
                "finalizing",
                "available",
                "failed",
                "quarantined",
                name="mediaavailability",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="staging",
            nullable=False,
        ),
    )
    op.add_column("media_objects", sa.Column("ingestion_idempotency_key", sa.String(length=255)))
    op.add_column("media_objects", sa.Column("failure_reason", sa.String(length=1024)))
    op.execute(
        "UPDATE media_objects SET availability = CASE WHEN available THEN 'available' "
        "ELSE 'staging' END, hash_algorithm = CASE WHEN content_hash IS NOT NULL "
        "THEN 'sha256' ELSE NULL END"
    )
    op.alter_column("media_objects", "original_filename", server_default=None)
    op.alter_column("media_objects", "availability", server_default=None)
    op.create_unique_constraint(
        op.f("uq_site_media_objects_site_id"),
        "media_objects",
        ["site_id", "ingestion_idempotency_key"],
    )
    op.create_check_constraint(
        op.f("ck_site_media_objects_available_metadata_complete"),
        "media_objects",
        "(availability = 'available' AND size_bytes IS NOT NULL AND "
        "content_hash IS NOT NULL AND hash_algorithm IS NOT NULL) OR "
        "availability <> 'available'",
    )
    op.drop_column("media_objects", "available")


def downgrade() -> None:
    op.add_column(
        "media_objects",
        sa.Column("available", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute("UPDATE media_objects SET available = (availability = 'available')")
    op.alter_column("media_objects", "available", server_default=None)
    op.drop_constraint(
        op.f("ck_site_media_objects_available_metadata_complete"),
        "media_objects",
        type_="check",
    )
    op.drop_constraint(op.f("uq_site_media_objects_site_id"), "media_objects", type_="unique")
    op.drop_column("media_objects", "failure_reason")
    op.drop_column("media_objects", "ingestion_idempotency_key")
    op.drop_column("media_objects", "availability")
    op.drop_column("media_objects", "hash_algorithm")
    op.drop_column("media_objects", "original_filename")

    op.drop_constraint(
        op.f("ck_site_storage_targets_safety_reserve_nonnegative"),
        "storage_targets",
        type_="check",
    )
    op.drop_column("storage_targets", "safety_reserve_bytes")
