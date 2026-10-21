FROM ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 AS uv
FROM node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build
FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY provider_alembic ./provider_alembic
COPY provider_alembic.ini ./provider_alembic.ini
COPY scripts ./scripts
COPY prompts ./prompts
COPY --from=frontend /app/frontend/dist /app/frontend/dist
RUN uv sync --frozen --no-dev && useradd --create-home --uid 10001 app && mkdir -p /app/.local/attachments /app/.local/embedding-cache && chown -R app:app /app/.local
USER app
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-sync", "--no-dev", "uvicorn", "aftersales.main:app", "--host", "0.0.0.0", "--port", "8000"]
