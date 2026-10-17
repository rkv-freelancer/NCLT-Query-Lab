# ============================================================================
# BASE: shared Python + uv setup
# ============================================================================
FROM python:3.12-slim-trixie AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/   

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /workspace

# ============================================================================
# DEVELOPMENT: tools only. Dependencies come from postCreateCommand (uv sync)
# against the bind-mounted repo, so `uv add` never needs an image rebuild.
# ============================================================================
FROM base AS development

# psql 18 from the PostgreSQL apt repo (Debian 13 itself only ships 17)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl make git \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && printf '%s\n' \
    'Types: deb' \
    'URIs: https://apt.postgresql.org/pub/repos/apt' \
    "Suites: ${VERSION_CODENAME}-pgdg" \
    'Components: main' \
    'Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc' \
    > /etc/apt/sources.list.d/pgdg.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

# ============================================================================
# PRODUCTION: deps layer first (cached), then source; non-root user
# ============================================================================
FROM base AS production

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --create-home app
USER app

CMD ["python", "-m", "nclt_lab.load"]  