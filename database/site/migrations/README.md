# Site Alembic Migrations

This is the independent Alembic revision history for each Site PostgreSQL database. It imports only `upm_site.persistence` metadata and uses `UPM_SITE_DATABASE_URL`.

Run from the repository root with uv:

```powershell
uv run alembic -c database/site/alembic.ini upgrade head
```

Site migrations never connect to or modify the Central database.
