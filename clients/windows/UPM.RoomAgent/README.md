# UPM Room Agent

`UPM.RoomAgent.exe` is the one Windows 11 product for room presentation computers,
upload kiosks, and combined Room Agent + Kiosk devices. The WinUI application hosts
the Agent runtime and loopback API in-process; it has no console window and requires
no separately launched backend.

## Build and run

```powershell
dotnet build .\clients\windows\UPM.RoomAgent\UPM.RoomAgent.csproj -c Debug
.\clients\windows\UPM.RoomAgent\bin\Debug\net10.0-windows10.0.22621.0\win-x64\UPM.RoomAgent.exe
```

Repeatable x64 release publish from a Windows developer prompt:

```powershell
dotnet restore .\clients\windows\UPM.RoomAgent\UPM.RoomAgent.csproj --runtime win-x64
dotnet publish .\clients\windows\UPM.RoomAgent\UPM.RoomAgent.csproj -c Release -r win-x64 -p:Platform=x64 --self-contained true
```

That is the complete room-computer startup workflow:

1. UPM Room Agent creates or loads its durable machine identity in
   `%ProgramData%\UPM\Agent`.
2. Site advertises `_upm-site._tcp.local` for standard LAN discovery consumers; the
   bundled Windows client uses the signed UPM UDP discovery protocol on multicast
   group `239.255.77.77:43820` for enrollment and address recovery.
3. It announces the durable Agent identity, machine name, Windows/Agent versions,
   capabilities, and supported roles.
4. Site exact-matches the Windows machine name to one existing Device when the match
   is unique, or registers the Agent as **Unassigned**.
5. Site returns a unique persistent Agent credential. Windows stores it with DPAPI
   `LocalMachine`; Site stores only its SHA-256 hash.
6. Site operators assign Event, Room, and Role from Site web or Site Manager. The
   Agent receives the assignment through the revision feed automatically.
7. Schedule, presentations, rotating slides, and branding reconcile without SMB.

When the Presentation Library setting is enabled, startup creates its configured
root immediately. The persisted default is the interactive user's
`Desktop\UPM Presentations`; a missing custom root is also created before the next
synchronization. Disabling publishing never deletes the managed cache or existing
operator files.

If Site uses DHCP, the Agent continues discovery after enrollment. A discovery with
the saved Site UUID updates the endpoint and synchronization resumes without
reprovisioning.

Closing the window hides it to the notification area while discovery, sync,
downloads, heartbeat, and offline reconciliation continue. The tray menu provides
**Open UPM Room Agent**, **Open Upload Kiosk**, **Sync Now**, **Status**, and explicit
**Exit UPM Room Agent**. Enable **Start UPM Room Agent with Windows** in Settings for
a per-user Windows startup entry.

Manual Site URL and internal identity controls exist only under **Settings → Site
Connection → Diagnostics — Advanced** for emergency troubleshooting.
