# Universal Presentation Management (UPM) Agent Instructions

UPM is a completely new product and clean rebuild. SpeakerReady is the previous/current product and may be used only as a requirements reference, operational-workflow reference, and lessons-learned reference.

**Do not copy SpeakerReady's architecture.**

## Mandatory workflow

Before modifying UPM:

1. Read this file.
2. Determine the smallest subsystem and direct dependencies relevant to the requested task.
3. Inspect the existing implementation within that scope before creating or changing anything.
4. Search for existing models, APIs, services, migrations, jobs, workers, utilities, UI components, and tests relevant to the task.
5. Reuse established architecture, models, APIs, services, utilities, and patterns wherever possible.
6. Do not create parallel or duplicate implementations.
7. Preserve established deployment, process, database, storage, configuration, authority, synchronization, security, and lifecycle boundaries.
8. Add or update tests for meaningful implementation work and run relevant validation.

Do not assume a capability is missing because the UI does not expose it. Inspect the relevant backend implementation first.

Conversely, a menu, placeholder page, model, migration, endpoint, or partially implemented component does not prove that a complete workflow is implemented.

## Documentation reading policy

Do not read the entire UPM documentation set for every task.

Documentation must be read according to the scope and architectural impact of the requested work.

### Routine bugs and narrowly scoped changes

For routine bugs, hotfixes, UI fixes, CSS/theme fixes, naming or formatting changes, logging changes, test fixes, validation fixes, and other narrowly scoped work:

- Do NOT read the complete `UPM_MASTER_ARCHITECTURE.md` by default.
- Do NOT read the ADR index or ADRs by default.
- Do NOT read `PRODUCT_REQUIREMENTS.md`, `FEATURE_MATRIX.md`, or `IMPLEMENTATION_STATUS.md` by default.
- Do NOT perform a repository-wide architecture review.
- Inspect only the smallest relevant subsystem, files, tests, and direct dependencies.
- Preserve existing architecture rather than re-evaluating it.
- Read additional documentation only if the implementation reveals a genuine architectural question or conflict.

### Focused feature work

For a focused feature that extends an existing subsystem:

- Inspect the existing subsystem and direct dependencies first.
- Read only the architecture sections, ADRs, requirements, or status documentation directly relevant to that feature.
- Do not read unrelated ADRs or documentation.
- Reuse the existing canonical models, APIs, workers, services, and synchronization mechanisms.
- Expand the documentation scope only when necessary to resolve an architectural dependency or conflict.

### Architecture-impacting changes

For changes affecting any of the following:

- PostgreSQL schemas or canonical data models
- Central/Site synchronization
- authority or ownership boundaries
- service or container boundaries
- storage architecture
- media ownership or replication
- security or authentication
- device enrollment or control architecture
- deployment architecture
- offline Site autonomy
- durable jobs/outbox architecture
- major new subsystems

Read:

1. the directly relevant sections of the UPM Master Architecture Specification;
2. only the applicable ADRs;
3. directly relevant Product Requirements or Feature Matrix entries where necessary; and
4. the existing implementation and migrations affected by the change.

Read the complete Master Architecture Specification only for genuinely cross-cutting architectural work, major new subsystems, or when explicitly requested.

### Architectural conflicts

The Master Architecture Specification and accepted ADRs remain authoritative even when they are not loaded for every routine task.

If implementation work reveals a potential conflict with established UPM architecture:

1. stop expanding the implementation;
2. inspect the directly relevant architecture documentation and ADRs;
3. report the conflict rather than silently changing architecture.

## Binding engineering rules

- Treat the Master Architecture Specification as authoritative. Stop and report a conflict instead of silently changing architecture.
- Follow [Development Rules](docs/development/DEVELOPMENT_RULES.md) and accepted ADRs when they are relevant to the work.
- Keep Central and Site independently deployable, independently restartable, and backed by separate PostgreSQL databases and migration histories.
- Never allow Site, Signage, or a client to read Central PostgreSQL directly. Use explicit, secure, versioned contracts and synchronization mechanisms.
- Preserve Site offline autonomy. Routine active-show operation must not require a live Central call after required deployment data is synchronized.
- Use PostgreSQL, explicit Alembic migrations, constraints, transactions, and UUID identity. Do not introduce SQLite.
- Names, labels, filenames, and imported strings are labels, not identity.
- Room and device assignments are server authoritative. A reconnecting client must not overwrite server state.
- Use the PostgreSQL durable job/outbox architecture for restart-sensitive, retryable, heavy, or show-critical work. Do not substitute in-memory background tasks.
- Keep services modular, independently testable, single-purpose, observable, and replaceable.
- Never commit passwords, tokens, certificates, API keys, `.env` files, or other secrets.
- Codex must not commit generated binary artifacts, screenshots, presentation files, spreadsheets, PDFs, videos, or archives unless that exact binary asset is an intentional repository dependency.
- Tests must generate required binary fixtures at runtime in temporary directories.
- Before completing a task, inspect the final diff against the task base for accidental binary files.
- Do not add fake UI actions. A control must perform a real action or clearly identify an unavailable capability.
- Update documentation only when implementation or an approved decision materially changes documented behavior.
- Significant architecture changes require a new or superseding ADR; do not rewrite historical ADRs.

## Scope discipline

Use the smallest reasonable inspection and implementation scope.

For routine work:

- Do not scan the entire repository when the relevant subsystem can be identified directly.
- Do not inspect unrelated services, applications, migrations, or tests.
- Do not rewrite or refactor unrelated code.
- Do not regenerate documentation merely because code changed.
- Do not introduce abstractions unless they solve the requested problem or prevent clear duplication.
- Prefer modifying an established implementation over introducing a second path.
- Run focused tests and validation first.
- Expand testing scope only when the change affects shared or cross-cutting behavior.

Repository-wide searches are appropriate when needed to locate an unknown implementation or verify that a shared contract has no additional consumers, but they should not become a default repository-wide review.

## Architecture change discipline

Future milestones fill in the predetermined UPM architecture.

Do not build a disposable "temporary v1" architecture with the intent to redesign it later.

Incremental work must complete a coherent part of the target design without weakening:

- Central/Site separation
- Site autonomy
- authority boundaries
- identity rules
- synchronization contracts
- durable processing
- security boundaries
- canonical media/data ownership

Architecture should be extended deliberately rather than replaced opportunistically during routine implementation work.

## Testing and completion discipline

Before completing implementation work:

1. Run focused tests for the changed subsystem.
2. Run relevant linting/static validation for modified files where available.
3. Inspect the final diff for unrelated changes.
4. Inspect the final diff for accidental binary artifacts.
5. Confirm migrations are included when persistent schema changes require them.
6. Confirm new durable/show-critical work uses the established durable processing architecture.
7. Report tests actually executed and any tests that could not be executed.

Do not claim validation that was not actually performed.

## Documentation map

These documents are references to load when relevant; they are not a mandatory reading list for every task.

- [Master Architecture](docs/architecture/UPM_MASTER_ARCHITECTURE.md)
- [Architecture Decision Records](docs/architecture/decisions/README.md)
- [Product Requirements](docs/product/PRODUCT_REQUIREMENTS.md)
- [SpeakerReady Lessons](docs/product/SPEAKERREADY_LESSONS.md)
- [Feature Matrix](docs/product/FEATURE_MATRIX.md)
- [Development Rules](docs/development/DEVELOPMENT_RULES.md)
- [Implementation Status](docs/development/IMPLEMENTATION_STATUS.md)

## Standard for future Codex prompts

Future implementation prompts should be concise and task-specific.

They should normally instruct Codex to:

> Read the root `AGENTS.md`.
>
> Inspect the smallest relevant subsystem and direct dependencies before making changes.
>
> Reuse existing models, APIs, services, migrations, workers, utilities, and UI components wherever possible.
>
> Do not create parallel implementations.
>
> Follow the documentation reading policy in `AGENTS.md`; do not read the full architecture, ADR set, or product documentation unless the scope of the task requires it.
>
> Keep the implementation focused on the requested change and run relevant focused tests.

### Routine bug/hotfix prompts

Routine bug and hotfix prompts should explicitly avoid unnecessary architecture/documentation review when useful:

> Do not review or modify ADRs, architecture documentation, or unrelated documentation for this task unless the implementation reveals an architectural conflict.

### Focused feature prompts

Focused feature prompts should identify the subsystem and any known architectural constraint but should not repeat the entire UPM architecture.

Read only directly relevant documentation.

### Architecture/data/synchronization prompts

Prompts involving architecture, canonical data models, synchronization, security, storage architecture, deployment boundaries, or major new subsystems should explicitly identify the relevant architectural areas to review.

Do not require reading unrelated ADRs or documentation.

## Prompt and context efficiency

UPM development should minimize unnecessary context consumption without sacrificing architectural correctness.

- Do not repeat the complete UPM architecture in implementation prompts.
- Do not require documents to be reread when they are unrelated to the requested change.
- Prefer task-specific acceptance criteria over broad repository reviews.
- Let Codex inspect existing endpoint names, models, workers, directory structure, and implementation details rather than embedding large amounts of repository information in prompts.
- Use architectural documentation as targeted guardrails, not mandatory context for every code change.
- Expand context only when the task genuinely requires it.
