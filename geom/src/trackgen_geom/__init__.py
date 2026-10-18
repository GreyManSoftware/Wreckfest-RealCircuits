"""Geometry core for the Wreckfest Track Generator.

``build_track_model(spec)`` turns a track spec into a :class:`TrackModel`, geometry facts, rule
results and SVG previews. Pure Python plus numpy; no Blender needed.
"""

__version__ = "0.1.0"

from .model import TrackModel  # noqa: E402
from .pipeline import GeometryIssue, GeometryWarning, Result, build_track_model  # noqa: E402

__all__ = ["GeometryIssue", "GeometryWarning", "Result", "TrackModel", "__version__", "build_track_model"]
