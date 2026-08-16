"""add Central storage-root management

Revision ID: 5d23c80ab411
Revises: bd84e21f6a53
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "5d23c80ab411"
down_revision: str | Sequence[str] | None = "bd84e21f6a53"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "storage_roots",
        sa.Column("storage_root_id", sa.UUID(), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("backend_type", sa.String(32), nullable=False, server_default="filesystem"),
        sa.Column("path", sa.String(2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_successful_check_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('staging', 'media')", name="ck_storage_roots_storage_root_role"),
        sa.CheckConstraint("backend_type = 'filesystem'", name="ck_storage_roots_storage_root_backend"),
        sa.CheckConstraint("path LIKE '/%'", name="ck_storage_roots_storage_root_path_absolute"),
    )
    op.create_index("uq_central_active_storage_role", "storage_roots", ["role"], unique=True,
                    postgresql_where=sa.text("enabled"))
    op.add_column("presentation_media_imports", sa.Column("staging_storage_root_id", sa.UUID()))
    op.create_foreign_key("fk_media_import_staging_root", "presentation_media_imports",
                          "storage_roots", ["staging_storage_root_id"], ["storage_root_id"],
                          ondelete="RESTRICT")

def downgrade() -> None:
    op.drop_constraint("fk_media_import_staging_root", "presentation_media_imports", type_="foreignkey")
    op.drop_column("presentation_media_imports", "staging_storage_root_id")
    op.drop_table("storage_roots")
