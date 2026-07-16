FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY social_archiver ./social_archiver
COPY scripts ./scripts

RUN uv sync --frozen

RUN mkdir -p downloads/instagram downloads/twitter logs data

CMD ["uv", "run", "python", "-m", "social_archiver.platforms.instagram", "daemon"]
