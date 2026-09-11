FROM ghcr.io/astral-sh/uv:0.8.12 AS uv
FROM python:3.13-slim AS build
COPY --from=uv /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY shared/python/pyproject.toml shared/python/pyproject.toml
COPY central/python/pyproject.toml central/python/pyproject.toml
COPY site/python/pyproject.toml site/python/pyproject.toml
COPY signage/python/pyproject.toml signage/python/pyproject.toml
RUN uv sync --frozen --no-dev --package upm-signage --no-install-workspace
COPY shared/python/src shared/python/src
COPY signage/python/src signage/python/src
COPY database/signage database/signage
RUN uv sync --frozen --no-dev --package upm-signage --no-editable
FROM python:3.13-slim
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/database /app/database
RUN useradd -u 10003 -m signage && mkdir /media && chown signage /media
USER signage
CMD ["uvicorn","upm_signage.api:create_app","--factory","--host","0.0.0.0","--port","8080"]
