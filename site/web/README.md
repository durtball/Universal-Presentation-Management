# UPM Site Web

The Site production web image is built by `Dockerfile` from the shared `web/` React/TypeScript source. It is independently deployed and uses only Site-local APIs for operational views, preserving Site autonomy during Central/WAN outages.

See `docs/development/frontend.md` and `ADR-0008`.
