# Coding Agent Instructions

1. Read `docs/architecture/UPM_MASTER_ARCHITECTURE.md` completely before substantial work.
2. Treat the Master Architecture Specification as authoritative.
3. Never silently change the architecture or introduce behavior that conflicts with it.
4. Stop and report any architectural conflict before proceeding.
5. Keep Central and Site deployment, process, database, storage, configuration, and lifecycle boundaries intact.
6. Never commit passwords, tokens, certificates, API keys, `.env` files, or other secrets.
7. Run validation relevant to the change before declaring work complete.
8. Prefer modular, independently testable, single-purpose components.
9. Avoid temporary architecture intended to be replaced later; milestones must fit the target design.
10. Update documentation when implementation decisions materially change documented behavior.
