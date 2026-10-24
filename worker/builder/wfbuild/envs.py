"""Environment presets (``environments.json``): stock textures and the stock objects each environment may use.

Pure Python (no bpy, no numpy), so the dressing planner and the web preview tooling can use it too.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = {
    "fields:": "data/art/levels/fields/textures/",
    "forest:": "data/art/levels/forest/textures/",
    "sandpit:": "data/art/levels/sandpit/textures/",
    "urban:": "data/art/levels/urban/textures/",
    "tex:": "data/art/textures/",
}
DEFAULT_ENV = "gravel_pit"


def stem(s: str) -> str:
    """'fields:x_c' -> 'data/art/levels/fields/textures/x_c' (a game path without extension)."""
    for p, full in PREFIX.items():
        if s.startswith(p):
            return full + s[len(p):]
    if not s.startswith("data/"):
        raise ValueError(f"texture stem without a known prefix: {s}")
    return s


def ns_of(layer: dict) -> str:
    """The layer's normal/AO/spec (_ns) map: explicit, or the albedo stem with _c -> _ns."""
    if layer.get("ns"):
        return stem(layer["ns"])
    c = stem(layer["c"])
    if not c.endswith("_c"):
        raise ValueError(f"cannot derive _ns from {c}")
    return c[:-2] + "_ns"


@lru_cache(maxsize=1)
def table() -> dict:
    with open(os.path.join(HERE, "environments.json"), encoding="utf-8") as f:
        return json.load(f)


def env(name: str | None) -> dict:
    envs = table()["environments"]
    return envs.get(name or DEFAULT_ENV, envs[DEFAULT_ENV])


def env_name(name: str | None) -> str:
    return name if name in table()["environments"] else DEFAULT_ENV


def asset_info(name: str) -> dict:
    if name.startswith("sponsor_"):  # our own sponsor boards (meshes, not stock objects)
        from . import sponsor  # noqa: PLC0415 - sponsor imports this module

        info = sponsor.asset_info(name)
        if info is not None:
            return info
    return table()["assets"].get(name, {"r": 1.0, "h": 2.0, "face": "+y"})


def allowed_assets(name: str | None) -> set[str]:
    """Every stock object an environment may place: the shared trackside sets plus its own lists."""
    e = env(name)
    out: set[str] = set()
    for v in table()["common"].values():
        out.update(v)
    for k in ("trees", "parkTrees", "bushes", "ground_props", "buildings", "cornerMarkers", "saplings"):
        out.update(e.get(k, []))
    out.update(e.get("foliage", {}).get("groups", []))
    # coastal extras: gorse clumps, shore rocks and cliffs, stone field walls, farmstead plans
    out.update(e.get("gorse", {}).get("assets", []))
    out.update(e.get("shore", {}).get("rocks", []) + e.get("shore", {}).get("cliffs", []))
    out.update(e.get("stoneWalls", {}).get("assets", []))
    out.update(a for a, _u, _v in e.get("farmsteads", {}).get("plan", []))
    for row in e.get("barriers", {}).values():
        out.update(row)
    out.add(e["crowdFence"])
    from . import sponsor  # noqa: PLC0415

    out.update(sponsor.asset_names())
    return out


def barrier_row(name: str | None, walls: str) -> list[str]:
    """The stock props that form the visible barrier for options.walls (concrete / tyres) in an environment."""
    return list(env(name).get("barriers", {}).get(walls, []))


def foliage(name: str | None) -> dict:
    """Foliage density settings with the defaults for environments that don't override them."""
    n = env_name(name)
    d = {"treeSpacing": 8.0 if n == "forest" else 11.0,
         "minTreeM": {"forest": 20.0, "sandpit": 60.0, "industrial": 30.0}.get(n, 24.0),
         "treeCap": 2600 if n == "forest" else 700, "bushCap": 300 if n == "forest" else 160,
         "saplingClusters": 45, "saplingCap": 800, "groundPropCap": 160, "groups": [], "groupCap": 0}
    d.update({k: v for k, v in env(n).get("foliage", {}).items() if not k.startswith("_")})
    return d


def texture_stems(name: str | None) -> set[str]:
    """Every stock texture stem an environment references (for the tests)."""
    e = env(name)
    out = set()
    layers = list(e["ground"].values()) + [lay for v in e["roads"].values() for lay in v]
    layers += [v for k, v in (e.get("runoff") or {}).items() if not k.startswith("_")]
    for lay in layers:
        out.add(stem(lay["c"]))
        out.add(ns_of(lay))
    for k in ("c", "n", "s"):
        if e["edgeDecal"].get(k):
            out.add(stem(e["edgeDecal"][k]))
    out.update(stem(x) for x in (e["macro"], e["horizon"]["c"], e["horizon"]["fog"], e["farGround"]))
    out.update(stem(x) for x in e.get("treeCards", []) + e.get("skylineCards", []))
    out.update(stem(v) for k, v in table()["transitionDecal"].items() if not k.startswith("_"))
    return out
