# Docker Infrastructure

Central and Site use separate production-oriented API Dockerfiles at
`central/Dockerfile` and `site/Dockerfile`. Both build from Python 3.13 and the
repository's committed uv workspace lock, but each installs and runs only its
own application package.

The Compose projects retain separate images, networks, volumes, configuration,
PostgreSQL services, and lifecycles. Worker, synchronization, and web services
remain explicit scaffold boundaries until their independent runtimes are
implemented.
