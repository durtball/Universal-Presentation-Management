# UPM Signage

The runnable Signage vertical slice is the independent `docker-compose.signage.yml` stack. It owns a PostgreSQL database and media volume and consumes only the authenticated Site projection API. Site unavailability is reported as source connectivity loss; cached schedule playback remains healthy and advances using cached UTC timestamps.

The bundled `/player/` page is browser-only: it retains its current DOM frame during a transient API outage, but browser or host restart durability comes from the local Signage API/PostgreSQL stack. A persistent unattended endpoint should run the player URL in OS-managed kiosk mode; redundant player supervision is not yet provided.

Configure separate Signage PostgreSQL credentials, `UPM_SIGNAGE_OPERATOR_PASSWORD`, `UPM_SIGNAGE_SITE_URL`, `UPM_SIGNAGE_SITE_CREDENTIAL`, and `UPM_SIGNAGE_EVENT_ID`. Configure the matching `UPM_SITE_SIGNAGE_PROJECTION_TOKEN` on Site. Pairing returns a per-display player credential exactly once.

Initial playback supports native room-door schedules in portrait `9:16`, landscape `16:9`, and an empty non-event playlist mode. PowerPoint and PDF playback are intentionally not advertised.
