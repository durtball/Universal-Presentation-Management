#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_NAME
readonly EXPECTED_REMOTE_PATH="durtball/Universal-Presentation-Management"
readonly COMPOSE_FILE="docker-compose.central.yml"

branch="main"
env_file=".env"
pull_source=true
build_images=true
wait_timeout=180

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [options]

Deploy UPM Central from a clean Linux Git checkout.

Options:
  --branch NAME       Deploy this origin branch (default: main)
  --env-file PATH     Compose environment file (default: .env)
  --no-pull           Deploy the current local commit without fetching
  --no-build          Reuse existing local images without pulling/building
  --wait-timeout SEC  Health wait timeout (default: 180)
  -h, --help          Show this help
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '==> %s\n' "$*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command is unavailable: $1"
}

while (($# > 0)); do
    case "$1" in
        --branch)
            (($# >= 2)) || fail "--branch requires a value"
            branch="$2"
            shift 2
            ;;
        --env-file)
            (($# >= 2)) || fail "--env-file requires a value"
            env_file="$2"
            shift 2
            ;;
        --no-pull)
            pull_source=false
            shift
            ;;
        --no-build)
            build_images=false
            shift
            ;;
        --wait-timeout)
            (($# >= 2)) || fail "--wait-timeout requires a value"
            wait_timeout="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown option: $1"
            ;;
    esac
done

[[ "$(uname -s)" == "Linux" ]] || fail "This deployment script requires Linux."
[[ "$branch" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "Invalid branch name: $branch"
[[ "$wait_timeout" =~ ^[1-9][0-9]*$ ]] || fail "--wait-timeout must be a positive integer"

require_command git
require_command docker
require_command curl
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 ('docker compose') is unavailable."
docker info >/dev/null 2>&1 || fail "Docker is unavailable or the current user cannot access the daemon."

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$repository_root"

git_root="$(git rev-parse --show-toplevel 2>/dev/null)" || fail "Expected a Git checkout."
[[ "$(cd -- "$git_root" && pwd -P)" == "$repository_root" ]] ||
    fail "Run the script from the UPM repository; resolved root was $git_root"
[[ -f "$COMPOSE_FILE" && -f "docs/architecture/UPM_MASTER_ARCHITECTURE.md" ]] ||
    fail "This does not appear to be the UPM repository."

remote_url="$(git remote get-url origin 2>/dev/null)" || fail "Git remote 'origin' is unavailable."
[[ "$remote_url" == *"$EXPECTED_REMOTE_PATH"* ]] ||
    fail "Unexpected origin remote: $remote_url"

[[ -f "$env_file" ]] ||
    fail "Environment file '$env_file' is missing. Copy .env.example to .env and set production secrets."
[[ "$env_file" != ".env.example" ]] ||
    fail ".env.example contains development placeholders and cannot be used for production deployment."

if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    git status --short >&2
    fail "Refusing to deploy with local Git changes. Commit, stash, or remove them first."
fi

current_branch="$(git branch --show-current)"
if $pull_source; then
    info "Fetching origin/$branch"
    git fetch --prune origin "$branch"
    if [[ "$current_branch" != "$branch" ]]; then
        if git show-ref --verify --quiet "refs/heads/$branch"; then
            git switch "$branch"
        else
            git switch --track -c "$branch" "origin/$branch"
        fi
    fi
    git merge --ff-only "origin/$branch"
elif [[ "$current_branch" != "$branch" ]]; then
    fail "--no-pull requires the current branch to be '$branch' (current: '$current_branch')."
fi

[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
    fail "The checkout changed unexpectedly during source update."

compose=(docker compose --env-file "$env_file" -f "$COMPOSE_FILE")
"${compose[@]}" config --quiet

if $build_images; then
    info "Updating external images"
    "${compose[@]}" pull caddy central-web central-postgres
    info "Building the versioned UPM Central application image"
    "${compose[@]}" build --pull central-api
else
    info "Reusing existing images (--no-build)"
fi

wait_for_health() {
    local service="$1"
    local deadline=$((SECONDS + wait_timeout))
    local container_id=""
    local health=""
    while ((SECONDS < deadline)); do
        container_id="$("${compose[@]}" ps --quiet "$service")"
        if [[ -n "$container_id" ]]; then
            health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id")"
            [[ "$health" == "healthy" ]] && return 0
            [[ "$health" == "unhealthy" ]] && return 1
        fi
        sleep 2
    done
    return 1
}

info "Starting Central PostgreSQL"
"${compose[@]}" up --detach central-postgres
wait_for_health central-postgres || {
    "${compose[@]}" logs --no-color central-postgres >&2 || true
    fail "central-postgres did not become healthy. Existing volumes were preserved."
}

info "Running the version-matched Central migration"
migration_create_args=(create --force-recreate)
if ! $build_images; then
    migration_create_args+=(--no-build)
fi
"${compose[@]}" "${migration_create_args[@]}" central-migrate
migration_id="$("${compose[@]}" ps --all --quiet central-migrate)"
[[ -n "$migration_id" ]] || fail "central-migrate container was not created."
docker start --attach "$migration_id" || true
migration_exit="$(docker inspect --format '{{.State.ExitCode}}' "$migration_id")"
if [[ "$migration_exit" != "0" ]]; then
    "${compose[@]}" logs --no-color central-migrate >&2 || true
    fail "Central migration failed with status $migration_exit. Existing application containers and volumes were preserved."
fi

info "Starting or updating Central application services"
application_up_args=(up --detach --remove-orphans)
if ! $build_images; then
    application_up_args+=(--no-build)
fi
if ! "${compose[@]}" "${application_up_args[@]}"; then
    "${compose[@]}" ps >&2 || true
    fail "Central application startup failed. Existing PostgreSQL and Caddy volumes were preserved."
fi

for service in central-postgres central-api central-worker central-sync; do
    if ! wait_for_health "$service"; then
        "${compose[@]}" logs --no-color "$service" >&2 || true
        fail "$service did not become healthy within ${wait_timeout}s."
    fi
    info "$service is healthy"
done

# Compose interpolation reads the env file, while Bash does not. Resolve the published port
# from Docker so custom CENTRAL_HTTP_PORT values remain supported without sourcing secrets.
published_endpoint="$("${compose[@]}" port caddy 80 | tail -n 1)"
published_port="${published_endpoint##*:}"
[[ "$published_port" =~ ^[0-9]+$ ]] || fail "Unable to resolve the Caddy HTTP port."
health_url="http://localhost:${published_port}/api/health"

health_response=""
for ((attempt = 1; attempt <= 30; attempt++)); do
    if health_response="$(curl --fail --silent --show-error --max-time 5 "$health_url" 2>/dev/null)"; then
        break
    fi
    sleep 2
done
[[ "$health_response" == *'"service":"upm-central"'* ]] ||
    fail "Caddy health response did not identify upm-central: $health_response"
[[ "$health_response" == *'"status":"foundation-ready"'* ]] ||
    fail "Caddy health response was not foundation-ready: $health_response"

commit_sha="$(git rev-parse HEAD)"
info "Caddy health endpoint passed: $health_url"
info "UPM Central deployment succeeded"
printf 'Deployed commit: %s\n' "$commit_sha"
printf 'Environment file: %s\n' "$env_file"
printf 'PostgreSQL volumes and production data were preserved.\n'
