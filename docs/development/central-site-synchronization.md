# Central/Site registration and synchronization

## Protocol and persistence

`UPM_SYNC_PROTOCOL_VERSION` is independent of application versions. Both databases have their own
outbox, receipts, monotonic source sequence, and directional cursor. Site status is Site-owned;
managed Site settings are Central-owned. Batches are capped at 100 events and invalid events receive
explicit per-event failures. Acknowledgement is recorded only after receiver commit.

The Site sync worker classifies timeouts, DNS/connectivity errors, HTTP 408/425/429, and 5xx as
retryable. HTTP 400/401/403/409/413/422 and invalid authority/schema are permanent. Existing outbox
leases prevent simultaneous claims; bounded exponential backoff and attempt limits survive restart.

## Local co-located deployment

Run from the repository root on the Linux MS-01. Put real random values in `.env`; do not deploy the
example placeholders.

```bash
cp .env.example .env
python3 - <<'PY'
import secrets
print('UPM_CENTRAL_ADMIN_TOKEN=' + secrets.token_urlsafe(48))
print('UPM_CENTRAL_CREDENTIAL_ISSUER_KEY=' + secrets.token_urlsafe(48))
print('UPM_SITE_CREDENTIAL_ENCRYPTION_KEY=' + secrets.token_urlsafe(48))
PY
# Copy the three generated lines into .env, then:
docker network inspect upm-integration >/dev/null 2>&1 || docker network create upm-integration
docker compose --env-file .env -f docker-compose.central.yml build
docker compose --env-file .env -f docker-compose.site.yml build
docker compose --env-file .env -f docker-compose.central.yml up -d
docker compose --env-file .env -f docker-compose.site.yml up -d
```

Approve enrollment and prove both directions:

```bash
set -a; . ./.env; set +a
SITE_ID=$(curl -fsS http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/central-registration | jq -r .site_id)
curl -fsS -X POST -H "X-UPM-Admin-Token: $UPM_CENTRAL_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{}' \
  "http://127.0.0.1:${CENTRAL_HTTP_PORT:-8080}/api/v1/admin/sites/$SITE_ID/approve"
sleep 5
curl -fsS -X POST http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/sync/heartbeat
curl -fsS -X PUT -H "X-UPM-Admin-Token: $UPM_CENTRAL_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"value":{"proof":"central-to-site"}}' \
  "http://127.0.0.1:${CENTRAL_HTTP_PORT:-8080}/api/v1/admin/sites/$SITE_ID/settings/sync-proof"
sleep 5
curl -fsS http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/managed-settings | jq
curl -fsS -H "X-UPM-Admin-Token: $UPM_CENTRAL_ADMIN_TOKEN" \
  http://127.0.0.1:${CENTRAL_HTTP_PORT:-8080}/api/v1/admin/sites | jq
```

Browser views are `/admin/sites` on Central and `/admin/central-registration` on Site. Credentials
are never returned by either status API.

## Failure and recovery procedure

```bash
# 1. Stop Central transport; Site API/local media stays available.
docker compose --env-file .env -f docker-compose.central.yml stop caddy central-api central-sync
curl -fsS http://127.0.0.1:${SITE_HTTP_PORT:-9080}/health
curl -fsS -X POST http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/sync/heartbeat
curl -fsS http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/central-registration | jq
curl -fsS -X POST http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/sync/retry-failed

# 2. Restart Central and verify the durable queued heartbeat drains.
docker compose --env-file .env -f docker-compose.central.yml start central-api central-sync caddy
sleep 10
curl -fsS http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/central-registration | jq

# 3. Restart the Site sync worker during delivery; receipts make redelivery idempotent.
docker compose --env-file .env -f docker-compose.site.yml restart site-sync
sleep 10

# 4. Revoke and verify normal sync is rejected without affecting Site-local health.
curl -fsS -X POST -H "X-UPM-Admin-Token: $UPM_CENTRAL_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"reason":"failure simulation"}' \
  "http://127.0.0.1:${CENTRAL_HTTP_PORT:-8080}/api/v1/admin/sites/$SITE_ID/revoke"
curl -fsS -X POST http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/sync/heartbeat
sleep 5
curl -fsS http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/central-registration | jq

# 5. Deliberately reopen enrollment, reset only the Site credential state, and approve again.
curl -fsS -X POST -H "X-UPM-Admin-Token: $UPM_CENTRAL_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{}' \
  "http://127.0.0.1:${CENTRAL_HTTP_PORT:-8080}/api/v1/admin/sites/$SITE_ID/reenroll"
curl -fsS -X POST http://127.0.0.1:${SITE_HTTP_PORT:-9080}/api/v1/central-registration/request
sleep 5
curl -fsS -X POST -H "X-UPM-Admin-Token: $UPM_CENTRAL_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{}' \
  "http://127.0.0.1:${CENTRAL_HTTP_PORT:-8080}/api/v1/admin/sites/$SITE_ID/approve"
sleep 10
```

For independent hosts, set `UPM_INTEGRATION_NETWORK_EXTERNAL=false` in each host's environment and
set `UPM_SITE_CENTRAL_URL` to the Central HTTPS address. No database URL or database credential
crosses deployments.
