"""Add searchable Signage-local asset metadata."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_signage"
down_revision = "0003_signage"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("assets")}
    additions = [
        ("event_id", sa.Column("event_id", postgresql.UUID())),
        ("name", sa.Column("name", sa.String(255), nullable=False, server_default="asset")),
        (
            "original_filename",
            sa.Column("original_filename", sa.String(1024), nullable=False, server_default="asset"),
        ),
        (
            "created_at",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        ),
    ]
    for name, column in additions:
        if name not in columns:
            op.add_column("assets", column)
    if "ix_assets_event_id" not in {item["name"] for item in inspector.get_indexes("assets")}:
        op.create_index("ix_assets_event_id", "assets", ["event_id"])


def downgrade():
    op.drop_index("ix_assets_event_id", table_name="assets")
    for name in ["created_at", "original_filename", "name", "event_id"]:
        op.drop_column("assets", name)
