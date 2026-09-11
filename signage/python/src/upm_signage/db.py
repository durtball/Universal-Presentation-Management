from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


def factory(settings: Settings):
    return sessionmaker(
        create_engine(settings.database_url, pool_pre_ping=True), expire_on_commit=False
    )
