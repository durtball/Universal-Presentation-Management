[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repositoryRoot

try {
    $requiredPaths = @(
        'AGENTS.md',
        'README.md',
        '.editorconfig',
        '.env.example',
        '.gitignore',
        '.dockerignore',
        '.python-version',
        'pyproject.toml',
        'uv.lock',
        'docker-compose.central.yml',
        'docker-compose.site.yml',
        'central/Dockerfile',
        'site/Dockerfile',
        'docs/architecture/UPM_MASTER_ARCHITECTURE.md',
        'docs/architecture/domain-data-foundation.md',
        'docs/architecture/decisions/ADR-0001-backend-persistence-stack.md',
        'docs/architecture/decisions/ADR-0002-site-media-storage.md',
        'central/api/README.md',
        'central/web/README.md',
        'central/workers/README.md',
        'central/sync/README.md',
        'central/caddy/README.md',
        'central/postgres/README.md',
        'site/api/README.md',
        'site/web/README.md',
        'site/workers/README.md',
        'site/sync/README.md',
        'site/media/README.md',
        'site/device-management/README.md',
        'site/caddy/README.md',
        'site/postgres/README.md',
        'clients/agent/README.md',
        'clients/kiosk/README.md',
        'clients/signage/README.md',
        'clients/room-client/README.md',
        'shared/contracts/README.md',
        'shared/models/README.md',
        'shared/schemas/README.md',
        'shared/utilities/README.md',
        'database/central/migrations/README.md',
        'database/central/alembic.ini',
        'database/central/migrations/env.py',
        'database/central/migrations/versions/0001_central_domain_initial_central_domain_foundation.py',
        'database/site/migrations/README.md',
        'database/site/alembic.ini',
        'database/site/migrations/env.py',
        'database/site/migrations/versions/0001_site_domain_initial_site_domain_foundation.py',
        'shared/python/pyproject.toml',
        'shared/python/src/upm_shared/identifiers.py',
        'shared/python/src/upm_shared/contracts/entities.py',
        'central/python/pyproject.toml',
        'central/python/src/upm_central/api.py',
        'central/python/src/upm_central/persistence/models.py',
        'site/python/pyproject.toml',
        'site/python/src/upm_site/api.py',
        'site/python/src/upm_site/persistence/models.py',
        'infrastructure/central/README.md',
        'infrastructure/site/README.md',
        'infrastructure/caddy/central/Caddyfile',
        'infrastructure/caddy/site/Caddyfile',
        'infrastructure/docker/README.md',
        'scripts/bootstrap-dev.ps1',
        'tests/integration/README.md',
        'tests/sync/README.md',
        'tests/system/README.md',
        '.github/workflows/foundation.yml'
    )

    $missingPaths = @($requiredPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
    if ($missingPaths.Count -gt 0) {
        throw "Required foundation paths are missing:`n$($missingPaths -join "`n")"
    }

    $emptyFiles = @($requiredPaths | Where-Object {
        (Test-Path -LiteralPath $_ -PathType Leaf) -and
        (Get-Item -Force -LiteralPath $_).Length -eq 0
    })
    if ($emptyFiles.Count -gt 0) {
        throw "Required foundation files are empty:`n$($emptyFiles -join "`n")"
    }

    $trackedEnvironmentFiles = @(git ls-files | Where-Object {
        $leafName = Split-Path -Leaf $_
        ($leafName -eq '.env' -or $leafName -like '.env.*') -and $leafName -ne '.env.example'
    })
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to inspect tracked files with Git.'
    }
    if ($trackedEnvironmentFiles.Count -gt 0) {
        throw "Forbidden environment files are tracked:`n$($trackedEnvironmentFiles -join "`n")"
    }

    $gitignore = Get-Content -Force -LiteralPath '.gitignore' -Raw
    if ($gitignore -notmatch '(?m)^\.env$' -or $gitignore -notmatch '(?m)^!\.env\.example$') {
        throw '.gitignore must ignore .env while allowing .env.example.'
    }

    $conflictMarkers = @(git grep -n -E '^(<<<<<<<|=======|>>>>>>>)' -- . ':!docs/architecture/UPM_MASTER_ARCHITECTURE.md' 2>$null)
    if ($LASTEXITCODE -gt 1) {
        throw 'Unable to scan for unresolved conflict markers.'
    }
    if ($conflictMarkers.Count -gt 0) {
        throw "Possible unresolved conflict markers found:`n$($conflictMarkers -join "`n")"
    }

    Write-Host "Foundation validation passed ($($requiredPaths.Count) required paths)."
    Write-Host 'No forbidden tracked environment files or conflict markers were found.'
    $global:LASTEXITCODE = 0
}
finally {
    Pop-Location
}
