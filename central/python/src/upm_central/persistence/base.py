"""Central SQLAlchemy metadata; never shared with Site."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_central_%(column_0_label)s",
    "uq": "uq_central_%(table_name)s_%(column_0_name)s",
    "ck": "ck_central_%(table_name)s_%(constraint_name)s",
    "fk": "fk_central_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_central_%(table_name)s",
}


class CentralBase(DeclarativeBase):
    """Declarative base for Central-owned and Central-coordinated records only."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
