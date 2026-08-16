"""Deployment contract tests for independent Central and Site Compose stacks."""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def compose_config(filename: str, environment: dict[str, str] | None = None) -> dict:
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
        env={**os.environ, **(environment or {})},
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("deployment", ["central", "site"])
def test_media_storage_uses_named_volume_development_defaults(deployment: str) -> None:
    service = compose_config(f"docker-compose.{deployment}.yml")["services"][
        f"{deployment}-media-storage"
    ]
    mounts = {mount["target"]: mount for mount in service["volumes"]}

    for role in ("staging", "media", "temp"):
        assert mounts[f"/storage/{role}"]["type"] == "volume"
        assert mounts[f"/storage/{role}"]["source"].endswith(f"{deployment}-storage-{role}")
    assert mounts["/state"]["type"] == "volume"


@pytest.mark.parametrize(
    ("deployment", "paths"),
    [
        (
            "central",
            {
                "staging": "/mnt/upm/staging/central",
                "media": "/mnt/upm/media/central",
                "temp": "/mnt/upm/temp/central",
            },
        ),
        (
            "site",
            {
                "staging": "/mnt/upm/staging/site",
                "media": "/mnt/upm/media/site",
                "temp": "/mnt/upm/temp/site",
            },
        ),
    ],
)
def test_media_storage_host_paths_resolve_to_isolated_bind_mounts(
    deployment: str, paths: dict[str, str]
) -> None:
    environment = {
        f"UPM_{deployment.upper()}_{role.upper()}_HOST_PATH": path for role, path in paths.items()
    }
    service = compose_config(f"docker-compose.{deployment}.yml", environment)["services"][
        f"{deployment}-media-storage"
    ]
    mounts = {mount["target"]: mount for mount in service["volumes"]}

    for role, path in paths.items():
        mount = mounts[f"/storage/{role}"]
        assert mount["type"] == "bind"
        assert mount["source"] == path
        assert mount["target"] == f"/storage/{role}"


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


@pytest.mark.parametrize("deployment", ["central", "site"])
def test_web_frontend_is_a_separate_production_service(deployment: str) -> None:
    config = compose_config(f"docker-compose.{deployment}.yml")
    services = config["services"]
    web = services[f"{deployment}-web"]

    assert web["build"]["dockerfile"] == f"{deployment}/web/Dockerfile"
    assert web["healthcheck"]["test"] == [
        "CMD",
        "wget",
        "-qO-",
        "http://127.0.0.1:8080/healthz",
    ]
    assert web["networks"] == {f"{deployment}-edge": None}
    assert services["caddy"]["depends_on"][f"{deployment}-web"]["condition"] == ("service_healthy")


@pytest.mark.parametrize("deployment", ["central", "site"])
def test_caddy_routes_api_before_shared_frontend(deployment: str) -> None:
    caddyfile = (REPOSITORY_ROOT / f"infrastructure/caddy/{deployment}/Caddyfile").read_text()

    assert f"reverse_proxy {deployment}-api:8080" in caddyfile
    assert f"name {deployment}-web" in caddyfile
    assert "port 8080" in caddyfile
    assert "refresh 5s" in caddyfile
    assert caddyfile.index("handle /api/*") < caddyfile.index("dynamic a")
