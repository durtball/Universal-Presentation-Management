"""merge Central storage and permanent-deletion migration branches

Revision ID: f18a6c42d9e7
Revises: 5d23c80ab411, d7f4a2c91b63
"""

from collections.abc import Sequence

revision: str = "f18a6c42d9e7"
down_revision: str | Sequence[str] | None = ("5d23c80ab411", "d7f4a2c91b63")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
