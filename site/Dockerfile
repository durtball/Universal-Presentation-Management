# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.8.12 AS uv

FROM python:3.13-slim AS builder

COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY shared/python/pyproject.toml shared/python/pyproject.toml
COPY central/python/pyproject.toml central/python/pyproject.toml
COPY site/python/pyproject.toml site/python/pyproject.toml

RUN uv sync --frozen --no-dev --package upm-site --no-install-workspace

COPY shared/python/src shared/python/src
COPY site/python/src site/python/src

RUN uv sync --frozen --no-dev --package upm-site --no-editable

FROM python:3.13-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN groupadd --gid 10001 upm \
    && useradd --uid 10001 --gid upm --no-create-home --shell /usr/sbin/nologin upm

COPY --from=builder --chown=upm:upm /app/.venv /app/.venv

USER upm
EXPOSE 8080

CMD ["uvicorn", "upm_site.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
