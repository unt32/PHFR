# syntax=docker/dockerfile:1
# Backend (FastAPI + osmnx/networkx). Data files are NOT baked in; mount them.
ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Build tools are needed in case pyrosm/shapely/etc. have no wheel for this Python.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libgeos-dev libspatialindex-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-api.txt .
RUN pip install -r requirements-api.txt

COPY app ./app

# Non-root user; UID should match the host user so bind-mounted files stay writable
# (MapService writes a fresh pickle cache when the .pbf is newer).
ARG UID=1000
RUN useradd --create-home --uid ${UID} appuser \
 && mkdir -p /data && chown appuser /data /app
USER appuser

ENV OSM_PBF_PATH=/data/moldova.osm.pbf \
    LOG_LEVEL=INFO

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
