"""Internal geometry HTTP service wrapping :func:`trackgen_geom.build_track_model`.

Run with (one worker; the build runs in a small thread pool)::

    uvicorn trackgen_geom.service:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log

Requires the ``service`` extra. The contract with the API (``src/Api/Geometry/GeometryClient.cs``) is
documented in ``geom/README.md`` and ``src/Api/README.md``.

Environment:

- ``GEOM_MAX_BODY_BYTES`` (default 8 MiB): larger request bodies get 413.
- ``GEOM_COMPUTE_TIMEOUT_S`` (default 15): a build that takes longer gets 503. Python can't kill a
  thread, so the timed-out build keeps its pool thread until it finishes.
- ``GEOM_MAX_CONCURRENCY`` (default 2): build threads; further requests wait (inside the timeout).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, is_dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import __version__, rules
from .model import to_plain
from .pipeline import GeometryIssue, Result, build_track_model
from .terrain import terrain_grid

__all__ = ["app", "settings"]


class Settings:
    def __init__(self) -> None:
        self.max_body_bytes = int(os.environ.get("GEOM_MAX_BODY_BYTES", str(8 * 1024 * 1024)))
        self.compute_timeout_s = float(os.environ.get("GEOM_COMPUTE_TIMEOUT_S", "15"))
        self.max_concurrency = int(os.environ.get("GEOM_MAX_CONCURRENCY", "2"))


settings = Settings()
_executor = ThreadPoolExecutor(max_workers=max(1, settings.max_concurrency), thread_name_prefix="geom-build")

# ---------------------------------------------------------------------------------------------
# Structured logging: one JSON object per line on stderr.
# ---------------------------------------------------------------------------------------------

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


log = logging.getLogger("trackgen_geom.service")
if not log.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(JsonFormatter())
    log.addHandler(_handler)
    log.setLevel(os.environ.get("GEOM_LOG_LEVEL", "INFO").upper())

# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------


class ServiceError(Exception):
    """Returned as ``{"detail": {"code", "message", ...}}`` with ``status``."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **extra}


def _error_response(status: int, detail: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


app = FastAPI(title="trackgen-geom", version=__version__, docs_url=None, redoc_url=None)


@app.exception_handler(ServiceError)
async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
    return _error_response(exc.status, exc.detail)


@app.exception_handler(Exception)
async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled error", exc_info=exc, extra={"path": request.url.path})
    return _error_response(500, {"code": "internal-error", "message": "Internal error in the geometry service."})


@app.middleware("http")
async def _access_log(request: Request, call_next: Any) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/healthz":
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "durationMs": round((time.perf_counter() - start) * 1000, 1),
                "requestId": request.headers.get("x-request-id"),
            },
        )
    return response


# ---------------------------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------------------------


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


async def _read_spec(request: Request) -> Any:
    """The ``spec`` of a ``{"spec": TrackSpec}`` body, reading at most ``max_body_bytes``."""
    limit = settings.max_body_bytes
    too_large = ServiceError(413, "body-too-large", f"The request body is larger than {limit} bytes.", limit=limit)
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > limit:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise too_large
    try:
        payload = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ServiceError(400, "bad-request", "The request body is not valid JSON.") from exc
    if not isinstance(payload, dict) or "spec" not in payload:
        raise ServiceError(400, "bad-request", 'The request body must be {"spec": TrackSpec}.')
    return payload["spec"]


async def _build(spec: Any) -> Result:
    """Run the build in the thread pool, bounded by the compute timeout."""
    loop = asyncio.get_running_loop()
    start = time.perf_counter()
    future = loop.run_in_executor(_executor, build_track_model, spec)
    try:
        result = await asyncio.wait_for(future, timeout=settings.compute_timeout_s)
    except TimeoutError:
        log.warning("build timed out", extra={"timeoutS": settings.compute_timeout_s})
        raise ServiceError(
            503, "compute-timeout", f"The geometry build took longer than {settings.compute_timeout_s:g} s."
        ) from None
    except Exception:
        log.exception("build failed")
        raise ServiceError(500, "internal-error", "Internal error in the geometry service.") from None
    log.info(
        "build",
        extra={
            "slug": result.model.slug if result.model else None,
            "valid": result.valid,
            "warnings": len(result.warnings),
            "errors": [e.code for e in result.errors],
            "computeMs": round((time.perf_counter() - start) * 1000, 1),
        },
    )
    return result


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _plain(value: Any) -> Any:
    """Dataclasses to camelCase dicts (values unchanged)."""
    if is_dataclass(value) and not isinstance(value, type):
        return {_camel(f.name): _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _evaluation(result: rules.EvaluationResult) -> dict[str, Any]:
    """Same shape as the API's ``EvaluationResult`` (plus ``valid``)."""
    return {**_plain(result), "valid": result.valid}


def _issues(issues: list[GeometryIssue]) -> list[dict[str, str]]:
    return [{"code": i.code, "message": i.message} for i in issues]


def _checked(result: Result) -> Result:
    """Raise 422 if the spec produced no geometry (schema-invalid, no main loop, or rejected)."""
    if not result.rule_result.schema_valid:
        raise ServiceError(
            422,
            "schema-invalid",
            "The spec does not match the track spec schema.",
            schemaErrors=_plain(result.rule_result.schema_errors),
        )
    if result.model is None or result.facts is None:
        errors = result.errors or [w for w in result.warnings if w.code == "main-loop-missing"]
        raise ServiceError(
            422,
            "geometry-rejected",
            "; ".join(e.message for e in errors) or "The sketch can't be turned into geometry.",
            errors=_issues(errors),
            warnings=_issues([w for w in result.warnings if w not in errors]),
        )
    return result


def _body(result: Result) -> dict[str, Any]:
    return {
        "version": __version__,
        "valid": result.valid,
        "facts": result.facts,
        "ruleResult": _evaluation(result.rule_result),
        "warnings": _issues(result.warnings),
        "errors": _issues(result.errors),
    }


def _model_summary(result: Result) -> dict[str, Any]:
    model = result.model
    assert model is not None
    return to_plain(
        {
            "formatVersion": model.format_version,
            "slug": model.slug,
            "lapLengthM": model.lap_length_m,
            "widthM": model.width_m,
            "bounds": model.bounds,
            "routes": [{"id": r.id, "closed": r.closed, "lengthM": r.length_m} for r in model.routes],
        }
    )


# ---------------------------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/validate")
async def validate(request: Request) -> JSONResponse:
    """Facts, rule result, warnings and errors for ``{"spec": TrackSpec}``."""
    result = _checked(await _build(await _read_spec(request)))
    return JSONResponse(_body(result))


@app.post("/preview")
async def preview(request: Request) -> JSONResponse:
    """``/validate`` plus the top-down ``svg``, ``elevationSvg`` and a ``modelSummary``."""
    result = _checked(await _build(await _read_spec(request)))
    return JSONResponse(
        {
            **_body(result),
            "svg": result.preview_svg,
            "elevationSvg": result.elevation_svg,
            "modelSummary": _model_summary(result),
        }
    )


@app.post("/model")
async def model(request: Request) -> JSONResponse:
    """``/validate`` plus the full TrackModel and a coarse ``terrain`` grid (the web editor's 3D view)."""
    result = _checked(await _build(await _read_spec(request)))
    assert result.model is not None
    model_dict = result.model.to_dict()
    loop = asyncio.get_running_loop()
    grid = await loop.run_in_executor(_executor, terrain_grid, model_dict)
    return JSONResponse({**_body(result), "model": model_dict, "terrain": grid})
