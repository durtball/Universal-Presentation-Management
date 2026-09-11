[CmdletBinding()]
param([string]$OutputRoot = "artifacts/windows")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$projects = @{
  "SiteManager" = "clients/site-manager/UPM.SiteManager.csproj"
  "RoomAgent" = "clients/windows/UPM.RoomAgent/UPM.RoomAgent.csproj"
  "Signage" = "clients/windows/UPM.Signage/UPM.Signage.csproj"
}
foreach ($entry in $projects.GetEnumerator()) {
  $project = Join-Path $root $entry.Value
  if (-not (Test-Path $project)) { throw "Requested Windows build is missing $($entry.Key) project: $project" }
  $output = Join-Path $root "$OutputRoot/$($entry.Key)"
  dotnet publish $project --configuration Release -p:Platform=x64 -p:PublishDir=$output
  if ($LASTEXITCODE -ne 0) { throw "$($entry.Key) publish failed." }
  $expected = if ($entry.Key -eq "SiteManager") { "UPM.SiteManager.exe" } elseif ($entry.Key -eq "RoomAgent") { "UPM.RoomAgent.exe" } else { "UPM.Signage.exe" }
  if (-not (Test-Path (Join-Path $output $expected))) { throw "$($entry.Key) publish did not produce $expected." }
}
$launcher = @'
$runtime = Get-ItemProperty -Path "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F1E7E2BD-C79A-42D0-8F24-6A7AFF675955}" -ErrorAction SilentlyContinue
if (-not $runtime) { throw "Microsoft Edge WebView2 Runtime is required by UPM Signage." }
$executable = Join-Path $PSScriptRoot "UPM.Signage.exe"
if (-not (Test-Path $executable)) { throw "UPM.Signage.exe is missing from this publication." }
Start-Process $executable -ArgumentList $args
'@
Set-Content -Path (Join-Path $root "$OutputRoot/Signage/Launch-UPM-Signage.ps1") -Value $launcher
Write-Host "Published Site Manager, Room Agent/Kiosk, and UPM Signage to $OutputRoot."
