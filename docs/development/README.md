# Development Documentation

UPM server development uses Python 3.13 and
[uv](https://docs.astral.sh/uv/) without global application dependency
installation.

```powershell
uv python install 3.13
uv sync --locked --all-packages
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not postgres"
```

PostgreSQL-backed migration and constraint tests require independent Central
and Site PostgreSQL databases. Configure them with
`UPM_CENTRAL_DATABASE_URL` and `UPM_SITE_DATABASE_URL`; never point both values
at the same database. Apply migrations independently:

```powershell
uv run alembic -c database/central/alembic.ini upgrade head
uv run alembic -c database/site/alembic.ini upgrade head
```

The root `pyproject.toml` owns development tools. `shared/python`,
`central/python`, and `site/python` remain separate workspace packages. See
`docs/architecture/domain-data-foundation.md` for ownership and data-model
details.
