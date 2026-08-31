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
