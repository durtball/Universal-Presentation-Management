# UPM Signage

The runnable Signage vertical slice is the independent `docker-compose.signage.yml` stack. It owns a PostgreSQL database and media volume and consumes only the authenticated Site projection API. Site unavailability is reported as source connectivity loss; cached schedule playback remains healthy and advances using cached UTC timestamps.

The bundled `/player/` page is browser-only: it retains its last verified manifest during a transient API outage. Full schedule advancement and restart durability require the autonomous local Signage API/PostgreSQL stack; a cached current screen is not a schedule database.

`clients/windows/UPM.Signage` provides the independent Windows endpoint. It embeds the real web designer, stores the paired display credential in Windows PasswordVault, bundles the player shell, and atomically retains the last schema-validated manifest for a server outage. The retained manifest is a last-screen fallback only. When Site is offline but the Signage server is running, scheduling continues for the full locally projected event horizon. When the Signage server itself is offline, the Windows endpoint cannot advance beyond its last manifest. Renderer failures are retried three times per five-minute window and then require operator action. The initial renderer supports native schedule templates; PowerPoint, PDF, and unverified media are unsupported.

Configure separate Signage PostgreSQL credentials, `UPM_SIGNAGE_OPERATOR_PASSWORD`, `UPM_SIGNAGE_SITE_URL`, `UPM_SIGNAGE_SITE_CREDENTIAL`, and `UPM_SIGNAGE_EVENT_ID`. Configure the matching `UPM_SITE_SIGNAGE_PROJECTION_TOKEN` on Site. Pairing returns a per-display player credential exactly once.

Initial playback supports native room-door schedules in portrait `9:16`, landscape `16:9`, and an empty non-event playlist mode. PowerPoint and PDF playback are intentionally not advertised.
