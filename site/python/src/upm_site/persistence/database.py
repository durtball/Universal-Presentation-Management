"""Site engine and session construction."""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from upm_site.config import SiteSettings


def create_site_engine(settings: SiteSettings) -> Engine:
    transfer_sessions = settings.transfer_pull_concurrency + settings.transfer_push_concurrency
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=max(5, transfer_sessions + 2),
        max_overflow=0,
    )


def create_site_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def site_session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory.begin() as session:
        yield session
