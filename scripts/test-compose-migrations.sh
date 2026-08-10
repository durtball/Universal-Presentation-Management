#!/usr/bin/env bash

set -Eeuo pipefail

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

[[ "$(uname -s)" == "Linux" ]] || fail "This smoke test requires Linux."
command -v docker >/dev/null 2>&1 || fail "Docker is required."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."
docker info >/dev/null 2>&1 || fail "The Docker daemon is unavailable."

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$repository_root"

test_root="$(mktemp -d "${TMPDIR:-/tmp}/upm-compose-migrations.XXXXXX")"
run_id="$(date +%s)-$$"
integration_network="upm-integration-migration-test-$run_id"
env_file="$test_root/test.env"
failure_override="$test_root/failing-migration.yml"
mkdir -p "$test_root/site-media"

cat >"$env_file" <<EOF
UPM_CENTRAL_PROJECT_NAME=upm-central-migration-test-$run_id
UPM_CENTRAL_API_IMAGE=upm-central-api:migration-test-$run_id
UPM_CENTRAL_WEB_IMAGE=upm-central-web:unbuilt-migration-test-$run_id
CENTRAL_HTTP_PORT=0
CENTRAL_POSTGRES_DB=upm_central_test
CENTRAL_POSTGRES_USER=upm_central_test
CENTRAL_POSTGRES_PASSWORD=compose-test-only-password
UPM_CENTRAL_DATABASE_URL=postgresql+psycopg://upm_central_test:compose-test-only-password@central-postgres:5432/upm_central_test
UPM_CENTRAL_ADMIN_TOKEN=compose-test-only-central-admin-token-at-least-32-characters
UPM_CENTRAL_CREDENTIAL_ISSUER_KEY=compose-test-only-central-issuer-key-at-least-32-characters
UPM_SITE_PROJECT_NAME=upm-site-migration-test-$run_id
UPM_SITE_API_IMAGE=upm-site-api:migration-test-$run_id
UPM_SITE_WEB_IMAGE=upm-site-web:unbuilt-migration-test-$run_id
SITE_HTTP_PORT=0
SITE_POSTGRES_DB=upm_site_test
SITE_POSTGRES_USER=upm_site_test
SITE_POSTGRES_PASSWORD=compose-test-only-password
UPM_SITE_DATABASE_URL=postgresql+psycopg://upm_site_test:compose-test-only-password@site-postgres:5432/upm_site_test
SITE_MEDIA_HOST_PATH=$test_root/site-media
UPM_SITE_MEDIA_MOUNT_PATH=/var/lib/upm/media
UPM_SITE_CREDENTIAL_ENCRYPTION_KEY=compose-test-only-site-encryption-key-at-least-32-characters
UPM_SITE_CENTRAL_URL=http://upm-central
UPM_INTEGRATION_NETWORK=$integration_network
UPM_INTEGRATION_NETWORK_EXTERNAL=true
EOF

cat >"$failure_override" <<'EOF'
services:
  central-migrate:
    command: ["sh", "-c", "echo intentional migration failure >&2; exit 42"]
EOF

central=(docker compose --env-file "$env_file" -f docker-compose.central.yml)
site=(docker compose --env-file "$env_file" -f docker-compose.site.yml)
failure=(docker compose --env-file "$env_file" -f docker-compose.central.yml -f "$failure_override" -p "upm-central-migration-failure-$run_id")

cleanup() {
    "${failure[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    "${site[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    "${central[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    docker network rm "$integration_network" >/dev/null 2>&1 || true
    if [[ "$test_root" == "${TMPDIR:-/tmp}"/upm-compose-migrations.* ]]; then
        rm -rf -- "$test_root"
    fi
}
trap cleanup EXIT

docker network create "$integration_network" >/dev/null

container_health() {
    local compose_name="$1"
    local service="$2"
    local container_id
    if [[ "$compose_name" == "central" ]]; then
        container_id="$("${central[@]}" ps --quiet "$service")"
    else
        container_id="$("${site[@]}" ps --quiet "$service")"
    fi
    [[ -n "$container_id" ]] || fail "$service is not running."
    [[ "$(docker inspect --format '{{.State.Health.Status}}' "$container_id")" == "healthy" ]] ||
        fail "$service is not healthy."
}

printf 'Building APIs and starting isolated Central and Site migration paths...\n'
"${central[@]}" build central-api
"${site[@]}" build site-api
"${central[@]}" up --detach --no-build --wait --wait-timeout 180 central-api
"${site[@]}" up --detach --no-build --wait --wait-timeout 180 site-api

for service in central-postgres central-api; do
    container_health central "$service"
done
for service in site-postgres site-api; do
    container_health site "$service"
done

central_migrate_id="$("${central[@]}" ps --all --quiet central-migrate)"
site_migrate_id="$("${site[@]}" ps --all --quiet site-migrate)"
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$central_migrate_id")" == "0" ]] ||
    fail "Central migration did not exit successfully."
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$site_migrate_id")" == "0" ]] ||
    fail "Site migration did not exit successfully."

"${central[@]}" exec -T central-postgres psql -U upm_central_test -d upm_central_test \
    -Atqc "SELECT to_regclass('public.alembic_version_central') IS NOT NULL, to_regclass('public.alembic_version_site') IS NULL" |
    grep -qx 't|t'
"${site[@]}" exec -T site-postgres psql -U upm_site_test -d upm_site_test \
    -Atqc "SELECT to_regclass('public.alembic_version_site') IS NOT NULL, to_regclass('public.alembic_version_central') IS NULL" |
    grep -qx 't|t'

printf 'Re-running an already-applied Central migration...\n'
"${central[@]}" create --force-recreate central-migrate
central_migrate_id="$("${central[@]}" ps --all --quiet central-migrate)"
docker start --attach "$central_migrate_id"
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$central_migrate_id")" == "0" ]] ||
    fail "Repeated Central migration did not exit successfully."
for service in central-api; do
    container_health central "$service"
done

printf 'Verifying migration failure blocks application startup...\n'
if "${failure[@]}" up --detach --no-build --wait --wait-timeout 60 central-api; then
    fail "Compose unexpectedly succeeded with an intentionally failing migration."
fi
failure_migrate_id="$("${failure[@]}" ps --all --quiet central-migrate)"
[[ -n "$failure_migrate_id" ]] || fail "Failing migration container was not created."
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$failure_migrate_id")" == "42" ]] ||
    fail "Intentional migration failure did not retain exit status 42."
[[ -z "$("${failure[@]}" ps --status running --quiet central-api)" ]] ||
    fail "central-api started despite migration failure."

printf 'Compose migration smoke test passed.\n'
