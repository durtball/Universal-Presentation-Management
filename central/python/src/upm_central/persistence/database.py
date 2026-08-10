"""Central engine and session construction."""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from upm_central.config import CentralDatabaseSettings


def create_central_engine(settings: CentralDatabaseSettings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)


def create_central_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def central_session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory.begin() as session:
        yield session
