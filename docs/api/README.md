# API Documentation

The Central program administration API is versioned under `/api/v1/admin` and includes:

- `/people` plus identity detail, update, deletion-impact, and protected deletion routes;
- `/events/{event_id}/participants`;
- `/events/{event_id}/sessions` and `/sessions/{session_id}/presenters`;
- `/events/{event_id}/presentations` plus presentation relationship routes;
- `/external-identifiers`;
- `/events/{event_id}/imports`, `/imports/{batch_id}`, reconciliation, and commit.

OpenAPI served by Central remains the exact request/response reference. Event-program propagation is
not an API push to a Site; mutations create the next ADR-0007 deployment revision for every active
deployment and use the existing protocol-v1 outbox transport.
