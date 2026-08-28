# UPM Site Manager

Production Windows foundation for live-event file intake and Site-mediated room control. The WinUI 3 shell provides Dashboard, Intake, Presentations, Rooms, Transfers, Review Sessions, Devices, Activity, and Settings navigation. Its graphite/cyan Fluent theme presents explicit empty/degraded states and never synthesizes Agent status.

The shared libraries implement typed cookie/CSRF Site API access, stable upload idempotency, asynchronous recursive intake with relative paths, a WAL/FULL-synchronous SQLite transfer queue, bounded parallel streaming uploads, SHA-256 verification identity, and retry backoff. Windows Credential Manager protects session material. Site Manager does not contain Agent functionality.

The current foundation directly supports login/session contracts, HTTP intake, durable queue recovery, command creation/observation, live runtime projections, and review/saveback control contracts. Executing commands, caching canonical media, launching PowerPoint through secure service/companion IPC, monitoring Office working copies, and uploading Agent saveback bytes remain responsibilities of the separate Agent product.
