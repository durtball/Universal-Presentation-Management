# Central Alembic Migrations

This is the independent Alembic revision history for the Central PostgreSQL database. It imports only `upm_central.persistence` metadata and uses `UPM_CENTRAL_DATABASE_URL`.

Run from the repository root with uv:

```powershell
uv run alembic -c database/central/alembic.ini upgrade head
```

Central migrations never connect to or modify the Site database.
