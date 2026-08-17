# Universal Presentation Management (UPM) Agent Instructions

UPM is a completely new product and clean rebuild. SpeakerReady is the previous/current product and may be used only as a requirements reference, operational-workflow reference, and lessons-learned reference. **Do not copy SpeakerReady's architecture.**

## Mandatory workflow

Before modifying UPM:

1. Read this file.
2. Read the complete [UPM Master Architecture Specification](docs/architecture/UPM_MASTER_ARCHITECTURE.md).
3. Read the [ADR index](docs/architecture/decisions/README.md) and every ADR applicable to the work.
4. Read the applicable [Product Requirements](docs/product/PRODUCT_REQUIREMENTS.md), [Feature Matrix](docs/product/FEATURE_MATRIX.md), and [Implementation Status](docs/development/IMPLEMENTATION_STATUS.md).
5. Inspect the existing implementation, including the backend, migrations, UI, and tests.
6. Search for existing models, APIs, services, migrations, jobs, UI components, and tests before creating anything.
7. Reuse the established architecture and existing components wherever possible.
8. Do not create parallel or duplicate implementations.
9. Preserve established deployment, process, database, storage, configuration, authority, synchronization, and lifecycle boundaries.
10. Add or update tests for meaningful implementation work and run relevant validation.

Do not assume a capability is missing because the UI does not expose it. Inspect the backend first. Conversely, a menu, placeholder page, model, or migration does not prove that a workflow is implemented.

## Binding engineering rules

- Treat the Master Architecture Specification as authoritative. Stop and report a conflict instead of silently changing architecture.
- Follow [Development Rules](docs/development/DEVELOPMENT_RULES.md) and accepted ADRs.
- Keep Central and Site independently deployable, independently restartable, and backed by separate PostgreSQL databases and migration histories.
- Never allow Site, Signage, or a client to read Central PostgreSQL directly. Use explicit, secure, versioned contracts and synchronization mechanisms.
- Preserve Site offline autonomy. Routine active-show operation must not require a live Central call after required deployment data is synchronized.
- Use PostgreSQL, explicit Alembic migrations, constraints, transactions, and UUID identity. Do not introduce SQLite.
- Names, labels, filenames, and imported strings are labels, not identity.
- Room and device assignments are server authoritative. A reconnecting client must not overwrite server state.
- Use the PostgreSQL durable job/outbox architecture for restart-sensitive, retryable, heavy, or show-critical work. Do not substitute in-memory background tasks.
- Keep services modular, independently testable, single-purpose, observable, and replaceable.
- Never commit passwords, tokens, certificates, API keys, `.env` files, or other secrets.
- Codex must not commit generated binary artifacts, screenshots, presentation files, spreadsheets,
  PDFs, videos, or archives unless that exact binary asset is an intentional repository dependency.
  Tests must generate required fixtures at runtime in temporary directories. Before completing a
  task, inspect the final diff against the task base for accidental binary files.
- Do not add fake UI actions. A control must perform a real action or clearly identify an unavailable capability.
- Update documentation when implementation or an approved decision materially changes documented behavior. Significant architecture changes require a new or superseding ADR; do not rewrite historical ADRs.

## Architecture change discipline

Future milestones fill in the predetermined UPM architecture. Do not build a disposable "temporary v1" architecture with the intent to redesign it later. Incremental work must complete a coherent part of the target design without weakening Central/Site separation, Site autonomy, authority boundaries, identity rules, or durable processing.

## Documentation map

- [Master Architecture](docs/architecture/UPM_MASTER_ARCHITECTURE.md)
- [Architecture Decision Records](docs/architecture/decisions/README.md)
- [Product Requirements](docs/product/PRODUCT_REQUIREMENTS.md)
- [SpeakerReady Lessons](docs/product/SPEAKERREADY_LESSONS.md)
- [Feature Matrix](docs/product/FEATURE_MATRIX.md)
- [Development Rules](docs/development/DEVELOPMENT_RULES.md)
- [Implementation Status](docs/development/IMPLEMENTATION_STATUS.md)

## Standard for future Codex prompts

Future implementation prompts should begin with instructions equivalent to:

> Read the root `AGENTS.md`, UPM Master Architecture, applicable ADRs, Product Requirements, Feature Matrix, and Implementation Status before making changes.
>
> Inspect the existing implementation before creating new components.
>
> Reuse existing models, APIs, services, migrations, and UI components wherever possible.
>
> Do not create parallel implementations.
