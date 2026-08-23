# Koi Fetch backend image — shared by the `backend` (API) and `worker`
# (background jobs) Compose services. The services differ only in their startup
# command: Compose passes the migration + uvicorn command (backend) or the
# worker entrypoint module (worker). The default CMD below runs the API for
# standalone `docker run`.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System timezone database so the TZ env var (Asia/Shanghai per PRD) is
# effective for container-local time; slim images ship without /usr/share/zoneinfo.
# (The PyPI `tzdata` package in requirements.txt already covers ZoneInfo lookups.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so dependency changes do not invalidate the app layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code (ports-and-adapters layout under app/).
COPY app ./app

# Runtime storage layout: SQLite dir, Bubble (temp), Pond (permanent).
# Compose overrides these paths with bind mounts; the directories also make a
# plain `docker run` work without mounts.
RUN mkdir -p \
    /app/data/db \
    /app/data/bubble/video /app/data/bubble/image /app/data/bubble/music \
    /app/data/pond/video /app/data/pond/image /app/data/pond/music

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
