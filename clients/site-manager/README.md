# UPM Site Manager

UPM Site Manager is the separate native WinUI 3 Windows operator, file-management, and Site-mediated control executable. It contains no Agent service, interactive Agent companion, PowerPoint launcher, presentation-computer cache runtime, or room-machine command execution. The supported boundary remains **Site Manager → Site → Agent**.

## Native shell

The shell has distinct navigation targets for Dashboard, Intake, Presentations, Rooms, Transfers, Review Sessions, Devices, Activity, and Settings. Disconnected screens expose explicit empty, unavailable, or unknown states and never synthesize Agent presence or operational data.

The normal initial window is 1280×800 and remains freely resizable/maximizable. The practical operator target starts at 1000×650 logical pixels. Adaptive behavior includes:

- `NavigationView` compact mode below 1000 logical pixels and expanded mode at 1320;
- a two-row Site/event toolbar below 1180 and a single-row toolbar from 1180 upward;
- wrapping dashboard metric cards and room cards at every width;
- presentation secondary-column reduction below 1180, with the full table at 1500;
- transfer speed/elapsed column reduction below 1300;
- page-owned vertical scrolling only where content can grow, with horizontal page scrolling disabled;
- logical-pixel sizing so Windows DPI scaling reflows the same breakpoints.

The default visual system uses Mica, deep graphite panels, illuminated cyan borders, restrained violet secondary accents, and animation only for future active work states.

## Build and packaging boundary

`UPM.SiteManager.csproj` produces an unpackaged, self-contained WinUI `WinExe` independently of UPM Agent. The Windows CI workflow restores and builds the executable and every shared Windows project, runs the tests, and verifies there is no Agent project dependency. A signed MSIX is intentionally not emitted yet because publisher identity, signing certificate, and final Store/enterprise visual assets are deployment inputs; adding those packaging inputs does not require changing the executable or Agent boundary.

## Windows build and manual Site validation

Run these commands from an elevated or developer PowerShell prompt at the repository root. Set the Site URI for the environment being tested; no appliance address is compiled into Site Manager.

```powershell
$ErrorActionPreference = 'Stop'
$SiteUri = Read-Host 'UPM Site URI (for example, http://site-host:9080)'

dotnet --version
dotnet restore .\clients\windows\UPM.Windows.Tests\UPM.Windows.Tests.csproj
dotnet build .\clients\windows\UPM.Windows.Core\UPM.Windows.Core.csproj -c Release --no-restore
dotnet build .\clients\windows\UPM.Windows.SiteApi\UPM.Windows.SiteApi.csproj -c Release --no-restore
dotnet build .\clients\windows\UPM.Windows.Transfers\UPM.Windows.Transfers.csproj -c Release --no-restore
dotnet build .\clients\windows\UPM.Windows.Shell\UPM.Windows.Shell.csproj -c Release --no-restore
dotnet test .\clients\windows\UPM.Windows.Tests\UPM.Windows.Tests.csproj -c Release --no-restore
dotnet restore .\clients\site-manager\UPM.SiteManager.csproj -p:Platform=x64
dotnet build .\clients\site-manager\UPM.SiteManager.csproj -c Release --no-restore -p:Platform=x64
pwsh .\scripts\validate-windows-client-boundaries.ps1

$SiteBaseUri = [Uri]::new($SiteUri.TrimEnd('/') + '/')
$Health = Invoke-RestMethod -Uri ([Uri]::new($SiteBaseUri, 'health')) -TimeoutSec 8
if ($Health.service -ne 'upm-site') { throw 'Endpoint is not UPM Site.' }
Start-Process .\clients\site-manager\bin\x64\Release\net10.0-windows10.0.22621.0\win-x64\UPM.SiteManager.exe
```

In the running application:

1. Select **+** or Settings → **ADD SITE**.
2. Enter the display name, `$SiteUri`, normal Site username, and password.
3. Select **TEST CONNECTION** and confirm that UPM Site is reachable.
4. Select **SAVE & CONNECT** and verify the canonical Site name/UUID and connected indicator.
5. Verify that EVENT contains the Site's actual `/api/v1/event-deployments` rows and select one.
6. Close and reopen Site Manager; confirm profile, secure session, and valid last event restore.
7. Select Settings → **LOGOUT**, reconnect, and confirm a password is required after logout.
8. Add a second Site profile, queue a file for each profile, switch the selected UI Site, and verify each request reaches its owning profile URL and canonical Site UUID.
9. Inspect `%LOCALAPPDATA%\UPM\SiteManager\logs` for sanitized startup/connection diagnostics. Passwords, cookies, and CSRF values must not appear.
