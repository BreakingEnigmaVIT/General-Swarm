FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Hatchling needs README.md + package dirs from pyproject before editable install
COPY pyproject.toml README.md ./
COPY core agents tools providers memory coordination observability cli api configs ./

RUN pip install --no-cache-dir -e ".[redis]"

# Remaining project files (examples, tests, docs, deploy, …)
COPY . .

# A repo-root `groq/` shadows the PyPI `groq` package when PYTHONPATH=/app (ImportError: AsyncGroq).
RUN rm -rf /app/groq

# Create runtime directories
RUN mkdir -p traces memory_store

# Non-root user
RUN useradd -m -u 1000 swarm && chown -R swarm:swarm /app
USER swarm

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8765

CMD ["python", "-m", "cli.main", "dashboard", "--host", "0.0.0.0", "--port", "8765"]

# Worker image (same build): override CMD with
#   python -m coordination.worker
# and set SWARM_WORKER_ROLE, SWARM_REDIS_URL, SWARM_STREAM_PREFIX (see docs/deployment.md).
