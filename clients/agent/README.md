# UPM Room Agent

The Room Agent is one Windows product and one local runtime for room, upload-kiosk,
and combined roles. `UPM.RoomAgent.Service` runs independently of operator UIs,
persists its provisioned/offline state in `%ProgramData%\UPM\Agent`, and exposes a
loopback-only HTTP contract on port `43821`. Presentation and rotating-slide bytes
arrive through authenticated application transfer contracts; SMB, UNC paths, and
mapped drives are not Agent synchronization mechanisms.

The reusable `UPM.Windows.Agent` library owns the versioned SQLite state, managed
storage layout, verified atomic downloads, offline intake queue, Windows-safe human
presentation library, and restricted verified-file launcher. Room Client and Kiosk
must use the service contract rather than opening its database or implementing a
second synchronization engine.

## Windows development startup

Use Windows 11 with the .NET 10 SDK and the Windows App SDK build prerequisites.
From a PowerShell prompt at the repository root:

```powershell
# Build the two runnable products.
dotnet restore .\clients\agent\UPM.RoomAgent.Service\UPM.RoomAgent.Service.csproj
dotnet restore .\clients\windows\UPM.RoomClient\UPM.RoomClient.csproj
dotnet build .\clients\agent\UPM.RoomAgent.Service\UPM.RoomAgent.Service.csproj -c Debug
dotnet build .\clients\windows\UPM.RoomClient\UPM.RoomClient.csproj -c Debug

# Terminal 1: run the background Agent (console mode for development).
.\clients\agent\UPM.RoomAgent.Service\bin\Debug\net10.0\UPM.RoomAgent.Service.exe

# Terminal 2: run the graphical Room Client / Kiosk companion.
.\clients\windows\UPM.RoomClient\bin\Debug\net10.0-windows10.0.22621.0\win-x64\UPM.RoomClient.exe
```

The Room Client always discovers the service at `http://127.0.0.1:43821`; it never
opens Agent SQLite. To provision:

1. In Site Manager, create/select a Device and assign its room with the existing
   Devices/Rooms workflow.
2. Configure its event and product role through
   `PUT /api/v1/devices/{device_id}/agent-configuration` (`room_agent`,
   `upload_kiosk`, or `room_agent_kiosk`).
3. Issue the one-time credential through
   `POST /api/v1/devices/{device_id}/agent-credential`.
4. In Room Client, open **Settings → Provisioning → Provision / Reprovision** and
   enter the Site URL, Device UUID, one-time credential, and device name.
5. Confirm **AGENT CONNECTED**, **SITE CONNECTED**, and a populated event/room.
   **Sync Now** signals the same automatic worker used by the 30-second fallback.
6. Choose **Upload Kiosk** or **Open Upload Kiosk**. Dedicated kiosk roles open the
   branded kiosk surface automatically; cached branding and intake remain available
   while Site is offline.

Useful checks:

```powershell
Invoke-RestMethod http://127.0.0.1:43821/api/v1/status
Invoke-RestMethod http://127.0.0.1:43821/api/v1/dashboard
Invoke-RestMethod -Method Post http://127.0.0.1:43821/api/v1/sync
```

Production installation registers `UPM.RoomAgent.Service.exe` as the automatic
**UPM Room Agent** Windows service. Its machine credential is protected with Windows
DPAPI (`LocalMachine` scope); Site stores only the SHA-256 credential hash.
