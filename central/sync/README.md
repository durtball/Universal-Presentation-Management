# Central Sync

Central sync runs independently with `python -m upm_central.worker --sync`. It connects only to Central PostgreSQL and claims transactional OutboxEvent rows; future transport handlers will turn those records into explicit API/wire operations.
