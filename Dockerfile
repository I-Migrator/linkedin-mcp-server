# -- Stage 1: Build virtual environment --
FROM python:3.13.13-slim-bookworm@sha256:cfdfc1c32835000e04bf476b7679121da79b4a1e21e2a352985b40b3c275c0c8 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --no-editable --compile-bytecode

COPY . .
RUN uv sync --frozen --no-dev --no-editable --compile-bytecode


# -- Stage 2: Production runtime --
FROM python:3.13.13-slim-bookworm@sha256:cfdfc1c32835000e04bf476b7679121da79b4a1e21e2a352985b40b3c275c0c8

RUN useradd -m -s /bin/bash pwuser

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/patchright

RUN patchright install-deps chromium && \
    patchright install chromium && \
    chmod -R 755 /opt/patchright && \
    rm -rf /var/lib/apt/lists/*

USER pwuser

ENTRYPOINT ["python", "-m", "linkedin_mcp_server"]
CMD []
