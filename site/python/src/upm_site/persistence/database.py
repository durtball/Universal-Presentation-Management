"""Site engine and session construction."""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from upm_site.config import SiteSettings


def create_site_engine(settings: SiteSettings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)


def create_site_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def site_session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory.begin() as session:
        yield session
