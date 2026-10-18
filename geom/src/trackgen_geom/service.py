"""Internal geometry HTTP service (validate/preview endpoints come later).

Run with: uvicorn trackgen_geom.service:app --host 0.0.0.0 --port 8000
Requires the ``service`` extra.
"""

from fastapi import FastAPI

from trackgen_geom import __version__

app = FastAPI(title="trackgen-geom", version=__version__)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
