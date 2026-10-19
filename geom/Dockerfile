# syntax=docker/dockerfile:1
# Build from the repo root with the builder as a named context (Compose passes it as `wfbuild`):
#   docker build -t trackgen-geom --build-context wfbuild=worker/builder geom
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# The builder's pure-numpy modules (wfbuild, with environments.json) for POST /scenery. Only the package and its
# pyproject: no Blender, no Blender scripts, no game files.
COPY --from=wfbuild pyproject.toml /tmp/wfbuild/pyproject.toml
COPY --from=wfbuild wfbuild /tmp/wfbuild/wfbuild
# numpy and jsonschema are core dependencies; the service extra adds FastAPI and uvicorn.
RUN pip install ".[service]" /tmp/wfbuild \
    && rm -rf /app/src /tmp/wfbuild \
    && python -c "import wfbuild.dressing, wfbuild.envs as e; assert e.table()['assets']" \
    && useradd --system --uid 10001 --no-create-home app
USER app
EXPOSE 8000
# One worker: builds run in the service's own small thread pool (GEOM_MAX_CONCURRENCY).
# The service writes structured JSON request logs, so uvicorn's access log is off.
CMD ["uvicorn", "trackgen_geom.service:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--timeout-graceful-shutdown", "5"]
