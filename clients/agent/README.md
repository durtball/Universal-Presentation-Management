# UPM Room Agent runtime

The Agent runtime is an internal component of the single operator-facing **UPM Room
Agent** application. `UPM.RoomAgent.exe` hosts this library and its loopback-only API
on `127.0.0.1:43821` automatically; operators never launch a service or backend
executable.

The reusable runtime owns `%ProgramData%\UPM\Agent`, versioned SQLite state, Site
discovery, automatic enrollment, synchronization, heartbeat, verified transfers,
offline intake, branding, rotating slides, the presentation library, and safe local
launch. Room and Kiosk surfaces use the same runtime and never open SQLite directly.

See [`../windows/UPM.RoomAgent/README.md`](../windows/UPM.RoomAgent/README.md) for the
one-application build and startup workflow.
