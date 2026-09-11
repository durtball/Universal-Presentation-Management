"""Persist editable layouts, immutable publications, display dimensions, and operator sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_signage"
down_revision = "0001_signage"
branch_labels = None
depends_on = None


def upgrade():
    # Revision 0001 pre-dated a frozen schema declaration and used the imported
    # application metadata. These guards make both an existing 0001 database
    # and a clean install deterministic without rewriting that historical file.
    inspector = sa.inspect(op.get_bind())
    display_columns = {item["name"] for item in inspector.get_columns("displays")}
    for name, column in [
        ("width", sa.Column("width", sa.Integer(), nullable=False, server_default="1080")),
        ("height", sa.Column("height", sa.Integer(), nullable=False, server_default="1920")),
        (
            "orientation",
            sa.Column("orientation", sa.String(16), nullable=False, server_default="portrait"),
        ),
    ]:
        if name not in display_columns:
            op.add_column("displays", column)
    layout_columns = {item["name"] for item in inspector.get_columns("layouts")}
    for _name, column in [
        ("event_id", sa.Column("event_id", postgresql.UUID(), nullable=True)),
        ("description", sa.Column("description", sa.Text(), nullable=False, server_default="")),
        ("mode", sa.Column("mode", sa.String(32), nullable=False, server_default="room_door")),
        ("width", sa.Column("width", sa.Integer(), nullable=False, server_default="1920")),
        ("height", sa.Column("height", sa.Integer(), nullable=False, server_default="1080")),
        (
            "orientation",
            sa.Column("orientation", sa.String(16), nullable=False, server_default="landscape"),
        ),
        (
            "safe_area",
            sa.Column(
                "safe_area",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        ),
        (
            "background",
            sa.Column(
                "background",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        ),
        (
            "elements",
            sa.Column(
                "elements",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
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
        (
            "updated_at",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        ),
        ("created_by", sa.Column("created_by", postgresql.UUID(), nullable=True)),
        (
            "draft_revision",
            sa.Column("draft_revision", sa.BigInteger(), nullable=False, server_default="1"),
        ),
        ("published_revision", sa.Column("published_revision", sa.BigInteger(), nullable=True)),
        ("status", sa.Column("status", sa.String(32), nullable=False, server_default="draft")),
    ]:
        if _name not in layout_columns:
            op.add_column("layouts", column)
    inspector = sa.inspect(op.get_bind())
    layout_indexes = {item["name"] for item in inspector.get_indexes("layouts")}
    if "ix_layouts_event_id" not in layout_indexes:
        op.create_index("ix_layouts_event_id", "layouts", ["event_id"])
    tables = set(inspector.get_table_names())
    if "layout_revisions" not in tables:
        op.create_table(
            "layout_revisions",
            sa.Column("layout_revision_id", postgresql.UUID(), primary_key=True),
            sa.Column("layout_id", postgresql.UUID(), nullable=False),
            sa.Column("revision", sa.BigInteger(), nullable=False),
            sa.Column("snapshot", postgresql.JSONB(), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_by", postgresql.UUID()),
            sa.UniqueConstraint("layout_id", "revision"),
        )
        op.create_index("ix_layout_revisions_layout_id", "layout_revisions", ["layout_id"])
    if "operator_users" not in tables:
        op.create_table(
            "operator_users",
            sa.Column("user_id", postgresql.UUID(), primary_key=True),
            sa.Column("username", sa.String(255), nullable=False, unique=True),
            sa.Column("normalized_username", sa.String(255), nullable=False, unique=True),
            sa.Column("display_name", sa.String(255), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("roles", postgresql.JSONB(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "operator_sessions" not in tables:
        op.create_table(
            "operator_sessions",
            sa.Column("session_id", postgresql.UUID(), primary_key=True),
            sa.Column("user_id", postgresql.UUID(), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("csrf_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.ForeignKeyConstraint(["user_id"], ["operator_users.user_id"], ondelete="CASCADE"),
        )
        op.create_index("ix_operator_sessions_user_id", "operator_sessions", ["user_id"])


def downgrade():
    op.drop_table("operator_sessions")
    op.drop_table("operator_users")
    op.drop_table("layout_revisions")
    op.drop_index("ix_layouts_event_id", table_name="layouts")
    for name in [
        "status",
        "published_revision",
        "draft_revision",
        "created_by",
        "updated_at",
        "created_at",
        "elements",
        "background",
        "safe_area",
        "orientation",
        "height",
        "width",
        "mode",
        "description",
        "event_id",
    ]:
        op.drop_column("layouts", name)
    for name in ["orientation", "height", "width"]:
        op.drop_column("displays", name)
