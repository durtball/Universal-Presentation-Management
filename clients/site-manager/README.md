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
