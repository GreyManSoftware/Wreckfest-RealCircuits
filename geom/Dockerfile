# syntax=docker/dockerfile:1
# Build from geom/: docker build -t trackgen-geom geom
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[service]" \
    && useradd --system --uid 10001 --no-create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "trackgen_geom.service:app", "--host", "0.0.0.0", "--port", "8000"]
