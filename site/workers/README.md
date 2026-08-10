# Site Workers

The runnable general worker is `python -m upm_site.worker`. It claims Site ProcessingJob and TransferJob rows only from Site PostgreSQL. `python -m upm_site.worker --once` performs a startup/database/queue-loop smoke test.

Site synchronization remains a separate process (`python -m upm_site.worker --sync`) and consumes Site OutboxEvent rows. See [the durable jobs development guide](../../docs/development/durable-jobs-and-outbox.md).
