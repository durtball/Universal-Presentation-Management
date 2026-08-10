# ADR-0004: Site Media Ingestion and Filesystem Finalization

- **Status:** Accepted
- **Date:** 2026-08-10
- **Scope:** Site-local upload staging, authoritative file publication, and recovery state

## Context

PostgreSQL and a Site filesystem cannot participate in one ACID transaction. Large uploads must be streamed, failed uploads must not appear authoritative, client filenames cannot be filesystem identity, and a Site must recover without Central.

## Decision

Each ingestion receives a UUIDv7 `media_object_id`. Its authoritative location is the existing `storage_target_id` plus a generated category/year/month/UUID object key. The original filename remains metadata only. Staging uses a private directory beneath the same StorageTarget so finalization stays on one filesystem.

Media availability uses this small state machine:

```text
staging -> finalizing -> available
    \             \-> finalizing (reconciliation required after publication/DB failure)
     \-> failed

quarantined is reserved for a future security decision.
```

The API streams to an exclusively created staging file while computing SHA-256. After size validation, metadata moves to `finalizing`. Publication creates the final pathname without replacement using a same-filesystem hard link, then removes the staging name. A final database transaction marks the object `available` and enqueues its inspection job. Workers select only available media.

An optional client idempotency key is unique per Site. A retry after success returns the same durable identity. Content hashes are not unique: equal content does not merge unrelated domain records.

## Recovery consequences

- A database failure before staging leaves no file.
- An interrupted stream is removed and its record becomes `failed`.
- A crash during streaming leaves a `staging` record and artifact eligible for age-based cleanup after active-record exclusion.
- A crash after publication but before the final transaction leaves `finalizing`; reconciliation verifies size and SHA-256 before completing availability and job enqueue.
- A crash after the final transaction is safe because an idempotent retry returns the available record.
- Staging cleanup never deletes active/recent uploads.

Resumable/chunked upload protocol details remain deferred. The durable identity, idempotency key, state model, logical object key, and same-target staging layout are compatible with a future protocol without replacing storage architecture.
