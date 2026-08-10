"""Site SQLAlchemy metadata; never shared with Central."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_site_%(column_0_label)s",
    "uq": "uq_site_%(table_name)s_%(column_0_name)s",
    "ck": "ck_site_%(table_name)s_%(constraint_name)s",
    "fk": "fk_site_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_site_%(table_name)s",
}


class SiteBase(DeclarativeBase):
    """Declarative base for Site-local and synchronized projection records only."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
