[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Assert-Command {
    param(
        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [string]$InstallHint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$Name' was not found. $InstallHint"
    }
}

Write-Host 'Checking UPM development prerequisites...'

Assert-Command -Name 'git' -InstallHint 'Install Git and ensure it is available on PATH.'
Assert-Command -Name 'docker' -InstallHint 'Install Docker Desktop or Docker Engine and ensure it is available on PATH.'

try {
    docker compose version | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw 'docker compose returned a non-zero exit code.'
    }
}
catch {
    throw 'Docker Compose v2 is required. Install or enable the Docker Compose plugin; no software was installed by this script.'
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$developmentDirectories = @(
    (Join-Path $repositoryRoot '.local/central'),
    (Join-Path $repositoryRoot '.local/site'),
    (Join-Path $repositoryRoot '.local/site/media'),
    (Join-Path $repositoryRoot '.local/test-results')
)

foreach ($directory in $developmentDirectories) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

Write-Host 'Git, Docker, and Docker Compose are available.'
Write-Host 'Prepared ignored local development directories under .local/.'
Write-Host 'No software was installed and no containers were started.'
