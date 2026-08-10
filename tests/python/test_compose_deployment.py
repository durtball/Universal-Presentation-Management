"""Deployment contract tests for independent Central and Site Compose stacks."""

import json
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def compose_config(filename: str) -> dict:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            filename,
            "config",
            "--format",
            "json",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("deployment", "database_url", "migration_command"),
    [
        (
            "central",
            "UPM_CENTRAL_DATABASE_URL",
            ["alembic", "-c", "database/central/alembic.ini", "upgrade", "head"],
        ),
        (
            "site",
            "UPM_SITE_DATABASE_URL",
            ["alembic", "-c", "database/site/alembic.ini", "upgrade", "head"],
        ),
    ],
)
def test_compose_migration_gate(
    deployment: str, database_url: str, migration_command: list[str]
) -> None:
    config = compose_config(f"docker-compose.{deployment}.yml")
    services = config["services"]
    migrate = services[f"{deployment}-migrate"]
    postgres = f"{deployment}-postgres"

    assert migrate["command"] == migration_command
    assert migrate["restart"] == "no"
    assert set(migrate["environment"]) == {"DATABASE_HOST", "UPM_COMPONENT", database_url}
    assert migrate["depends_on"] == {postgres: {"condition": "service_healthy", "required": True}}
    assert migrate["networks"] == {f"{deployment}-internal": None}

    for role in ("api", "worker", "sync"):
        dependencies = services[f"{deployment}-{role}"]["depends_on"]
        assert dependencies[f"{deployment}-migrate"]["condition"] == (
            "service_completed_successfully"
        )
        assert dependencies[postgres]["condition"] == "service_healthy"


def test_central_and_site_migrations_are_packaged_and_isolated() -> None:
    central_dockerfile = (REPOSITORY_ROOT / "central/Dockerfile").read_text()
    site_dockerfile = (REPOSITORY_ROOT / "site/Dockerfile").read_text()

    assert "COPY database/central database/central" in central_dockerfile
    assert "database/site" not in central_dockerfile
    assert "COPY database/site database/site" in site_dockerfile
    assert "database/central" not in site_dockerfile


def test_migrations_use_the_same_image_as_their_application() -> None:
    for deployment in ("central", "site"):
        services = compose_config(f"docker-compose.{deployment}.yml")["services"]
        image = services[f"{deployment}-api"]["image"]
        assert services[f"{deployment}-migrate"]["image"] == image
        assert services[f"{deployment}-worker"]["image"] == image
        assert services[f"{deployment}-sync"]["image"] == image
        assert "build" in services[f"{deployment}-api"]
        assert "build" not in services[f"{deployment}-migrate"]
        assert "build" not in services[f"{deployment}-worker"]
        assert "build" not in services[f"{deployment}-sync"]


def test_linux_deployment_script_enforces_safe_production_flow() -> None:
    script = (REPOSITORY_ROOT / "scripts/deploy-central-linux.sh").read_text()

    assert '[[ "$(uname -s)" == "Linux" ]]' in script
    assert 'env_file=".env"' in script
    assert '[[ "$env_file" != ".env.example" ]]' in script
    assert "git status --porcelain --untracked-files=all" in script
    assert 'git fetch --prune origin "$branch"' in script
    assert 'git merge --ff-only "origin/$branch"' in script
    assert "migration_create_args=(create --force-recreate)" in script
    assert "docker start --attach" in script
    assert "central-postgres central-api central-worker central-sync" in script
    assert 'curl --fail --silent --show-error --max-time 5 "$health_url"' in script
    assert "down --volumes" not in script

    migration_position = script.index("migration_create_args=(create --force-recreate)")
    application_position = script.index("application_up_args=(up --detach --remove-orphans)")
    assert migration_position < application_position


def test_site_caddy_preserves_media_paths_and_health_compatibility() -> None:
    caddyfile = (REPOSITORY_ROOT / "infrastructure/caddy/site/Caddyfile").read_text()

    health_route = caddyfile.index("handle /api/health")
    media_route = caddyfile.index("handle /api/*")
    assert "rewrite * /health" in caddyfile
    assert health_route < media_route
