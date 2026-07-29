# MA Signal Monitor — web frontend + in-process scheduled ingestion.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # The web app runs ingestion on an interval inside the same process.
    RUN_SCHEDULER=true \
    DB_PATH=data/state.db

WORKDIR /app

# Install build/runtime tooling. curl is handy for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching), then the package.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip \
    && pip install ".[web]"

COPY config ./config

# Archive DB + WAL files live on a mounted volume.
RUN mkdir -p data logs
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "ma_signal_monitor.web.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
