# ADR-0001: Backend and Persistence Stack

- **Status:** Accepted
- **Date:** 2026-08-10
- **Scope:** Central and Site server-side APIs, workers, synchronization services, and related processing services

## Context

UPM requires independently deployable Central and Site services, PostgreSQL from the first implementation, explicit migration histories, language-neutral client contracts, and durable background processing. The initial architecture intentionally deferred the backend language and persistence tooling.

Windows Agent, Kiosk, Signage, and Room Client technology remains a separate decision. Those clients must be able to consume UPM contracts without depending on Python implementation types.

## Decision

UPM server-side services use:

- Python 3.13
- FastAPI for separate Central and Site HTTP applications
- Pydantic v2 for application and transport validation
- SQLAlchemy 2.x modern typed APIs for persistence models
- psycopg 3 as the only PostgreSQL driver
- Alembic for explicit PostgreSQL migrations
- PostgreSQL as the authoritative database platform
- `pyproject.toml` and uv for dependency, environment, workspace, and lockfile management
- generated OpenAPI and JSON Schema as language-neutral external contract representations

The repository uses an uv workspace with independently packaged `upm-shared`, `upm-central`, and `upm-site` Python projects. This permits deliberate code reuse while keeping Central and Site applications, SQLAlchemy metadata, sessions, configuration, and migration histories separate.

The root development project is not a deployable combined UPM application. It coordinates tooling and tests only.

## Persistence boundaries

Central and Site each have their own:

- SQLAlchemy declarative base and `MetaData`
- PostgreSQL connection configuration
- session factory
- Alembic configuration, environment, revision graph, and version table
- independently runnable upgrade/downgrade lifecycle

Shared persistence helpers may define value types or mixins, but must not define a shared declarative base, shared metadata registry, shared session, or cross-database relationship.

ORM models are persistence details. They are not API contracts and are not the synchronization protocol. Pydantic contracts and explicit API/event schemas form the boundary between deployments and languages.

## Identifier generation

UPM entity identifiers are PostgreSQL-native UUID values generated in the application/domain layer. New identifiers use UUIDv7 for time ordering and distributed generation.

Python 3.13 does not provide the required standard-library UUIDv7 generator. UPM therefore uses the small pure-Python [`uuid6`](https://pypi.org/project/uuid6/) dependency, which supports Python 3.13 and RFC 9562 UUIDv7. The exact resolved version is committed in `uv.lock`. This dependency can be removed when the minimum supported Python version provides a suitable standard implementation and compatibility tests pass.

Display names, filenames, titles, labels, and imported row numbers are never entity identity keys.

## Dependency workflow

- Developers install and run dependencies through uv, not global `pip` installs.
- `uv sync --locked --all-packages --all-groups` creates the local environment from `pyproject.toml` and `uv.lock`.
- `uv run` executes formatting, linting, tests, Alembic, and development commands in the managed environment.
- Dependency changes update both the applicable `pyproject.toml` and the committed lockfile.

## Consequences

- Central and Site can evolve independently without database coupling.
- External clients consume stable OpenAPI/JSON Schema rather than Python or SQLAlchemy objects.
- PostgreSQL-specific behavior is tested against PostgreSQL; SQLite is not a fallback.
- The server stack is Linux/Docker-oriented and does not constrain future Windows client technology.
- Separate packages and migration histories add some deliberate structure, but avoid a future architectural split or migration rewrite.
