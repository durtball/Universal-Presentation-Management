"""Record player versions in heartbeat state."""

import sqlalchemy as sa
from alembic import op

revision = "0005_signage"
down_revision = "0004_signage"
branch_labels = None
depends_on = None


def upgrade():
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("playback_state")}
    if "player_version" not in columns:
        op.add_column("playback_state", sa.Column("player_version", sa.String(64)))


def downgrade():
    op.drop_column("playback_state", "player_version")
