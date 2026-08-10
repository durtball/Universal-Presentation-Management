# Central Workers

The runnable general worker is `python -m upm_central.worker`. It claims Central ProcessingJob and TransferJob rows only from Central PostgreSQL. `python -m upm_central.worker --once` performs a startup/database/queue-loop smoke test.

Central synchronization remains a separate process (`python -m upm_central.worker --sync`) and consumes Central OutboxEvent rows. See [the durable jobs development guide](../../docs/development/durable-jobs-and-outbox.md).
