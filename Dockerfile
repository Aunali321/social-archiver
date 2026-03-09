FROM python:3.13-slim

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY insta_archiver ./insta_archiver
COPY main.py ./

# Install dependencies
RUN uv sync --frozen

# Create necessary directories
RUN mkdir -p downloads/likes downloads/saved downloads/shared logs

# Run archiver
CMD ["uv", "run", "python", "-m", "insta_archiver"]
