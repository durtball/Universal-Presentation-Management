# Site Sync

Site sync runs independently with `python -m upm_site.worker --sync`. It requires only Site PostgreSQL, so queued outbound work survives Central and WAN outages. Future transport handlers will publish through explicit secure APIs after connectivity returns.
