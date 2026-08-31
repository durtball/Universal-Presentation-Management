# UPM Room Client

`UPM.RoomClient.exe` is the interactive WinUI 3 companion for **UPM Room Agent**.
It contains the room dashboard, graphical settings, and fullscreen upload kiosk in
one executable. Every operational action uses the loopback Agent API at
`http://127.0.0.1:43821`; this project has no SQLite or Site API dependency.

The executable is produced at:

```text
bin\Debug\net10.0-windows10.0.22621.0\win-x64\UPM.RoomClient.exe
```

See [`../../agent/README.md`](../../agent/README.md) for exact PowerShell build,
startup, provisioning, connectivity, and kiosk instructions.
