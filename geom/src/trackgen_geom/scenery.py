"""The builder's scenery plan for a TrackModel, for the web editor's 3D preview.

This runs the worker's own planner (``wfbuild.dressing``, pure numpy, from ``worker/builder``; installed into
geom's environment as the ``trackgen-wfbuild`` package, see ``worker/builder/pyproject.toml``), so the preview
shows the same placements a build writes to ``scenery.json`` (format ``trackgen-scenery/1``, documented in
``worker/docs/scenery-manifest.md``). Positions are TrackModel coordinates; the build's placement transform is
not applied (``transform`` is the identity).

The planner takes about 0.3-3 s, so the service computes it only on ``POST /scenery``, never on ``/model``.
"""

from __future__ import annotations

from typing import Any


class SceneryUnavailable(RuntimeError):
    """``wfbuild`` is not installed (the geom image or venv was built without ``worker/builder``)."""


def plan_scenery(spec: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """The ``trackgen-scenery/1`` manifest for ``model`` (``TrackModel.to_dict()``) built from ``spec``,
    plus ``warnings`` (the builder geometry's, e.g. ``ground-fallback``)."""
    try:
        from wfbuild import dressing
        from wfbuild.geometry import TrackGeometry
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise SceneryUnavailable(str(exc)) from exc
    geo = TrackGeometry(model, spec)
    scenery, _ = dressing.plan(geo)
    return {**scenery.to_json(), "warnings": list(geo.warnings)}
