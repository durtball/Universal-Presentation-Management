# Deployment Documentation

## Media storage targets

Central and each Site run their own `*-media-storage` service on the private deployment network.
Development uses persistent named volumes for **Default Temporary Storage** and **Default Media
Storage**; container recreation does not remove those volumes.

Production storage is mounted by Linux and explicitly exposed to only the local storage service:

```yaml
volumes:
  - /mnt/upm-temp:/storage/temp
  - /mnt/upm-media:/storage/media
  - /mnt/raid/upm-media:/storage/raid
  - /mnt/nas/upm-media:/storage/nas
```

Set `UPM_CENTRAL_STORAGE_TARGETS_JSON` or `UPM_SITE_STORAGE_TARGETS_JSON` to a JSON array containing
a stable UUID, friendly `name`, container `internal_path`, and compatible `roles`. NAS, SMB, and NFS
filesystems are mounted and monitored by the Linux host; UPM sees only the explicitly exposed
target. Set a distinct random `UPM_CENTRAL_MEDIA_STORAGE_TOKEN` or
`UPM_SITE_MEDIA_STORAGE_TOKEN`. Never publish the storage service port or proxy it through Caddy.

## MS-01 UPM Central deployment

UPM Central is deployed from `/opt/upm/source` as an independent Docker Compose project. It owns
its PostgreSQL volume, internal network, edge network, Caddy state, API, worker, and synchronization
services. A co-located Site remains a separate Compose project with its own database, storage,
configuration, networks, and lifecycle.

### One-time preparation

Install Git, Docker Engine with the Compose v2 plugin, and curl. Grant the deployment account
access to the Docker daemon. Clone the repository at `/opt/upm/source`, then create the ignored
production environment file:

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /opt/upm
git clone https://github.com/durtball/Universal-Presentation-Management.git /opt/upm/source
cd /opt/upm/source
cp .env.example .env
chmod 600 .env
```

Replace every placeholder Central database password in `.env` with a strong production value and
keep `CENTRAL_POSTGRES_PASSWORD` consistent with `UPM_CENTRAL_DATABASE_URL`. Configure the desired
project name and HTTP port. Do not use `.env.example` as a production secret store and never commit
`.env`. Site values may remain unused when only the Central Compose file is deployed, but should be
configured separately before enabling a Site deployment.

For an existing PostgreSQL volume, `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` are
initialization inputs; editing them does not rename the existing database/user or change its stored
password. Keep the existing database and user names unless performing a separately planned database
administration change. To replace the development placeholder password on the current MS-01 volume,
run the PostgreSQL password prompt during a maintenance window using the environment that currently
works:

```bash
docker compose --env-file .env.example -f docker-compose.central.yml \
  exec central-postgres psql -U upm_central_local -d upm_central \
  -c '\password upm_central_local'
```

Then immediately put that same new password in both `CENTRAL_POSTGRES_PASSWORD` and the password
component of `UPM_CENTRAL_DATABASE_URL` in `.env`. The interactive prompt avoids putting the secret
in shell history. If the production volume was initialized with different database/user names, use
those existing names instead.

### Initial deployment and updates

After the deployment change is merged to `main`, initial deployment and normal updates use the same
command:

```bash
cd /opt/upm/source
./scripts/deploy-central-linux.sh --branch main
```

The script requires a clean checkout, fetches `origin/main`, permits only a fast-forward update,
validates `.env` and Compose, updates images, starts PostgreSQL, runs migration, updates the
long-running Central containers without deleting volumes, waits for health, verifies the Caddy
endpoint, and prints the deployed commit SHA. Use
`--no-pull` only to deploy the already checked-out `main` commit and `--no-build` only when the
correct application and supporting images are already present locally. Run `--help` for all
options.

### Migration and health behavior

`central-postgres` must become healthy before `central-migrate` runs. The one-shot service uses the
same image as the Central API and runs:

```text
alembic -c database/central/alembic.ini upgrade head
```

Only after it exits successfully can `central-api`, `central-worker`, and `central-sync` start.
Re-deployment safely reruns `upgrade head`; an already-current database is a successful no-op. If a
migration fails on a fresh deployment, dependent services remain blocked. On an update, the
previous long-running containers remain in place because migration runs before they are updated.
The script prints migration logs and exits nonzero. The expected Caddy health response at
`http://localhost:8080/api/health` is:

```json
{"service":"upm-central","status":"foundation-ready"}
```

The port follows `CENTRAL_HTTP_PORT` when configured differently.

### Data preservation and rollback

The deployment script never runs `docker compose down --volumes` and never deletes PostgreSQL or
production media. Before any release containing schema changes, take and verify a Central
PostgreSQL backup. Application rollback is safe only when the previous application version is
compatible with the migrated schema. Otherwise restore the release-specific pre-migration backup
or use a separately reviewed downgrade procedure. Do not improvise a generic production
`alembic downgrade`.

Inspect current state and migration output with:

```bash
docker compose --env-file .env -f docker-compose.central.yml ps --all
docker compose --env-file .env -f docker-compose.central.yml logs central-migrate
curl --fail http://localhost:8080/api/health
```

Never use `docker compose down --volumes` during routine deployment or recovery.
