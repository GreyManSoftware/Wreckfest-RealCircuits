# geom

`trackgen_geom`: the pure-Python geometry core (stroke → spline, crossings, elevation, bounds).
It is shared by the internal FastAPI geometry service and the Blender worker, and is testable without Blender.

```powershell
py -m venv .venv
$env:PIP_CACHE_DIR = 'E:\Source\wreckfest\.local\pip-cache'
.\.venv\Scripts\python -m pip install -e ".[service,test]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m uvicorn trackgen_geom.service:app --port 8000   # GET /healthz
```

Python types for the track spec will be generated from `../schema/` (do not hand-edit generated files).
