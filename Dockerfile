# Stage 1: resolve dependencies into a venv
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Lockfile only, so this layer caches until dependencies change
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: the WhatsApp bridge, a Go daemon the archiver supervises when
# WHATSAPP_BRIDGE_DIR is set
FROM golang:1.26-bookworm AS bridge

WORKDIR /src
COPY wabridge/ ./
RUN go build -o /wabridge .

# Stage 3: the viewer, a static SvelteKit build the API serves
FROM oven/bun:1-slim AS frontend

WORKDIR /fe
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

# Stage 4: runtime
FROM python:3.13-slim

# yt-dlp muxes with ffmpeg; without it every video download fails at the last step
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=bridge /wabridge /usr/local/bin/wabridge
COPY social_archiver ./social_archiver
COPY scripts ./scripts
COPY --from=frontend /fe/build ./frontend/build

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    DOWNLOADS_DIR=/downloads \
    LOGS_DIR=/logs

VOLUME ["/data", "/downloads", "/logs"]

CMD ["python", "-m", "social_archiver.api"]
