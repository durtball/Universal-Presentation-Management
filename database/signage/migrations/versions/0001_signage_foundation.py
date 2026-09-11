"""Independent Signage domain foundation."""

from alembic import op
from upm_signage import models  # noqa: F401
from upm_signage.db import Base

revision = "0001_signage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind())


def downgrade():
    Base.metadata.drop_all(op.get_bind())
