# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.14.7
ARG UV_VERSION=0.11.23

FROM ghcr.io/astral-sh/uv:${UV_VERSION}@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm@sha256:416f0db2a2b561945630cef9877a7ea0581b27449eb9fd9df42f03e1b74b5b63 AS dependencies

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_NO_CACHE=1

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:${PYTHON_VERSION}-slim-bookworm@sha256:416f0db2a2b561945630cef9877a7ea0581b27449eb9fd9df42f03e1b74b5b63 AS runtime

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Cinegraph" \
      org.opencontainers.image.description="Spoiler-aware episodic media intelligence" \
      org.opencontainers.image.source="https://github.com/CaptainVC/Cinegraph" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.version="${VERSION}"

ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 cinegraph \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/cinegraph \
       --create-home cinegraph \
    && mkdir -p /app /app/knowledge /home/cinegraph/.cache \
    && chown -R cinegraph:cinegraph /app /home/cinegraph

# The runtime only executes the locked virtual environment; remove the base
# interpreter's package installers and bundled ensurepip wheels to reduce attack
# surface and avoid shipping unused packaging tooling.
RUN ensurepip_bundle="$(python -c 'import ensurepip; print(ensurepip.__path__[0] + "/_bundled")')" \
    && python -m pip uninstall --yes setuptools pip \
    && rm -rf "$ensurepip_bundle"

COPY --from=dependencies --chown=cinegraph:cinegraph /opt/venv /opt/venv
WORKDIR /app
COPY --chown=cinegraph:cinegraph src ./src
COPY --chown=cinegraph:cinegraph scripts ./scripts
COPY --chown=cinegraph:cinegraph migrations ./migrations
COPY --chown=cinegraph:cinegraph alembic.ini pyproject.toml uv.lock README.md ./
COPY --chown=cinegraph:cinegraph knowledge/catalogue.json ./knowledge/catalogue.json

USER cinegraph
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"
CMD ["python", "scripts/run_api.py"]
