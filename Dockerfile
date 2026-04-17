FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cache)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[redis]"

# Copy source
COPY . .

# Create runtime directories
RUN mkdir -p traces memory_store

# Non-root user
RUN useradd -m -u 1000 swarm && chown -R swarm:swarm /app
USER swarm

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8765

CMD ["python", "-m", "cli.main", "dashboard", "--host", "0.0.0.0", "--port", "8765"]
