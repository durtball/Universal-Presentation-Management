"""Alembic environment for the Site database only."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from upm_site.persistence import models as site_models  # noqa: F401
from upm_site.persistence.base import SiteBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SiteBase.metadata
VERSION_TABLE = "alembic_version_site"
ENUM_CHECK_SUFFIXES = (
    "_assetkind",
    "_devicerole",
    "_jobstatus",
    "_mediacategory",
    "_mediaavailability",
    "_sourcesystem",
    "_storagehealth",
    "_storagetype",
    "_syncstate",
)


def include_object(object_, name: str | None, type_: str, reflected: bool, compare_to) -> bool:
    """Ignore reflected non-native Enum checks that SQLAlchemy owns through column types."""
    del object_, compare_to
    return not (
        reflected
        and type_ == "check_constraint"
        and name is not None
        and name.endswith(ENUM_CHECK_SUFFIXES)
    )


def database_url() -> str:
    url = os.environ.get("UPM_SITE_DATABASE_URL")
    if not url:
        raise RuntimeError("UPM_SITE_DATABASE_URL is required for Site migrations")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Site migrations require a postgresql+psycopg:// URL")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
