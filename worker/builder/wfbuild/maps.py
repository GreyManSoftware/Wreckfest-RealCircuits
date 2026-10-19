"""Per-track blend, colour and clutter maps for the stock-style #blend materials (pure numpy).

All maps are square over the map frame (``frame(geo)``: x0, y0, size, covering the terrain extent) and are
sampled through UV2 (``Mesh.set_map_uv``). Written as uncompressed TGA (bottom-left origin, so v = 0 is the
frame's south edge as in Blender) under ``<root>/data/track/<slug>/textures/``; the pipeline converts them with
``bimage -auto`` to ``.bmap`` inside the mod.

| Map | Channels | Used by |
|---|---|---|
| ``<slug>_blend_terrain_c`` | R dirt / sand-pebbles, G grass, B third ground (farm field, forest floor, sandbank, concrete) | terrain, shoulders (slot 6) |
| ``<slug>_blend_road_c`` | R main surface, G variant patches, B worn edge / dirt | road (slot 6) |
| ``<slug>_color_terrain_c`` | soft-light colour, 128 neutral: environment tint, wear near the road, crop tints | terrain, shoulders (slot 9) |
| ``<slug>_color_road_c`` | grey 128 +- noise, darker racing line, dusty edges | road (slot 9) |
| ``<slug>_clutter_t`` | G grass 5-80 m from the road (stock fields13), R bushes, B flowers, alpha 128 | terrain (slot 11) |
"""
from __future__ import annotations

import os

import numpy as np

from . import config as C
from .noise import Noise2D, seed_for

NAMES = ("blend_terrain_c", "blend_road_c", "color_terrain_c", "color_road_c", "clutter_t")


def frame(geo) -> tuple[float, float, float]:
    x0, x1, y0, y1 = geo.extent
    return (x0, y0, max(x1 - x0, y1 - y0))


def rel_path(slug: str, name: str) -> str:
    return f"data/track/{slug}/textures/{slug}_{name}"


def smooth01(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def write_tga(path: str, rgb: np.ndarray, alpha: np.ndarray | None = None) -> None:
    """Uncompressed 24/32-bit TGA, bottom-left origin; ``rgb`` rows run from v = 0 (south) upwards."""
    h, w = rgb.shape[:2]
    a = alpha is not None
    hdr = bytearray(18)
    hdr[2] = 2
    hdr[12:14] = w.to_bytes(2, "little")
    hdr[14:16] = h.to_bytes(2, "little")
    hdr[16] = 32 if a else 24
    hdr[17] = 8 if a else 0
    px = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)[:, :, ::-1]  # BGR
    if a:
        px = np.concatenate([px, np.clip(np.rint(alpha), 0, 255).astype(np.uint8)[:, :, None]], axis=2)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(bytes(hdr))
        f.write(np.ascontiguousarray(px).tobytes())


def zone_mask(zones, fr, n: int) -> np.ndarray:
    x0, y0, size = fr
    c = (np.arange(n) + 0.5) / n * size
    gx, gy = np.meshgrid(x0 + c, y0 + c)
    m = np.zeros((n, n))
    for z in zones:
        d = np.hypot(gx - z.x, gy - z.y)
        m = np.maximum(m, smooth01((z.r - d) / max(z.r * 0.35, 3.0)))
    return m


def generate(geo, field_, planner, root: str, res: int = C.MAP_RES) -> dict:
    """Write the five maps for this track; returns {name: data path without extension} and the frame."""
    slug = geo.model["slug"]
    fr = frame(geo)
    x0, y0, size = fr
    n = res
    env_name = planner.env_name
    e = planner.e
    d = field_.resample(n, fr)  # distance to the road edge, negative on the road
    px = size / n
    nz = lambda what, feat, oct=3: Noise2D(seed_for(slug, what), fr, feat, oct).grid(n)  # noqa: E731
    n_big, n_mid, n_fine = nz("map-big", 300.0), nz("map-mid", 60.0), nz("map-fine", 12.0, 2)
    c = (np.arange(n) + 0.5) / n * size
    gx, gy = np.meshgrid(x0 + c, y0 + c)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
    zones = zone_mask(planner.sc.zones, fr, n)

    # ---- terrain blend: R dirt near the road and where people/vehicles stand, G grass, B third ground
    dj = d + (n_mid - 0.5) * 6.0 + (n_fine - 0.5) * 3.0
    r = smooth01((7.0 - dj) / 5.0)                               # verge dirt, fading out 2-12 m from the edge
    r = np.maximum(r, zones * 0.85)
    r = np.maximum(r, smooth01((n_big - 0.72) / 0.08) * 0.6 * smooth01((d - 30) / 20))  # odd dirt patches
    b = np.zeros_like(d)
    if env_name == "forest":
        trees = planner.tree_noise.grid(n)
        b = smooth01((trees - planner.tree_thr + 0.04) / 0.08) * smooth01((d - 14.0) / 8.0)
    elif env_name == "farmland" and planner.parcels is not None:
        fld = planner.parcels.is_field(pts).reshape(n, n) & (planner.parcels.border(pts).reshape(n, n) > 3.0)
        b = fld * smooth01((d - 40.0) / 10.0)
    elif env_name == "sandpit":
        b = smooth01((n_big - 0.5) / 0.12) * smooth01((d - 25.0) / 15.0)
    elif env_name == "gravel_pit":
        b = smooth01((n_mid - 0.58) / 0.1) * smooth01((d - 15.0) / 10.0)
    elif env_name == "industrial":
        b = np.maximum(smooth01((n_mid - 0.6) / 0.1), zones) * smooth01((d - 10.0) / 6.0)
    b = b * (1 - r)
    g = np.clip(1.0 - r - b, 0.0, 1.0)
    s = np.maximum(r + g + b, 1e-6)
    blend_terrain = np.stack([r / s, g / s, b / s], axis=2) * 255.0

    # ---- road blend: R main, G variant patches, B worn edges
    rg = smooth01((n_mid - 0.55) / 0.12) * 0.8
    rb = smooth01((1.6 + d + (n_fine - 0.5) * 1.2) / 1.2)          # last ~1.5 m inside each edge
    rb = np.maximum(rb, smooth01((n_big - 0.7) / 0.1) * 0.5)
    rg = rg * (1 - rb)
    rr_ = np.clip(1.0 - rg - rb, 0.0, 1.0)
    s = np.maximum(rr_ + rg + rb, 1e-6)
    blend_road = np.stack([rr_ / s, rg / s, rb / s], axis=2) * 255.0

    # ---- terrain colour (soft-light; 128 neutral)
    mean = np.array(e["colour"]["mean"], dtype=np.float64)
    std = float(e["colour"]["std"])
    col = np.empty((n, n, 3))
    var = (n_big - 0.5) * 2.0 * std + (n_mid - 0.5) * std
    hue = (nz("map-hue", 400.0, 2) - 0.5) * std
    col[:, :, 0] = mean[0] + var + hue
    col[:, :, 1] = mean[1] + var - hue * 0.3
    col[:, :, 2] = mean[2] + var * 0.8 - hue * 0.5
    wear = smooth01((10.0 - d) / 8.0) * 10.0 + zones * 8.0
    col -= wear[:, :, None]
    if env_name == "farmland" and planner.parcels is not None:
        tints = np.array(e["colour"].get("fieldTints", [[150, 140, 90]]), dtype=np.float64)
        t_id = planner.parcels.tint(pts).reshape(n, n) % len(tints)
        stripes = 0.5 + 0.5 * np.sin(planner.parcels.local(pts)[0].reshape(n, n) * planner.parcels.w / 3.0)
        fc = tints[t_id] + (stripes[:, :, None] - 0.5) * 18.0
        col = col * (1 - b[:, :, None]) + fc * b[:, :, None]
    color_terrain = col

    # ---- road colour: grey, darker racing line in the middle, dusty edges
    cr = 126.0 + (n_mid - 0.5) * 16.0 + (n_fine - 0.5) * 8.0
    hw = float(np.median(geo.main.w)) / 2.0
    centre = smooth01((d + hw - 3.5) / -3.0)                     # within ~3 m of the centreline
    cr -= centre * 10.0
    cr += smooth01((1.5 + d) / 1.5) * 10.0
    color_road = np.repeat(cr[:, :, None], 3, axis=2)

    # ---- clutter mask: G grass 5-80 m from the road, not on dirt or fields; sparse R bushes, B flowers
    lo, hi = C.GRASS_BAND
    band = smooth01((dj - lo) / 3.0) * smooth01((hi - dj) / 15.0)
    grass = band * smooth01((n_fine - 0.3) / 0.25) * (1 - r) * (1 - b * 0.9)
    bushes = band * smooth01((n_mid - 0.7) / 0.08) * (1 - r) * 0.5
    flowers = band * smooth01((n_fine - 0.75) / 0.1) * (1 - r) * 0.6
    clutter = np.stack([bushes, grass, flowers], axis=2) * 255.0
    clutter_a = np.full((n, n), 128.0)

    out_dir = os.path.join(root, "data", "track", slug, "textures")
    paths = {}
    for name, rgb, a in (("blend_terrain_c", blend_terrain, None), ("blend_road_c", blend_road, None),
                         ("color_terrain_c", color_terrain, None), ("color_road_c", color_road, None),
                         ("clutter_t", clutter, clutter_a)):
        write_tga(os.path.join(out_dir, f"{slug}_{name}.tga"), rgb, a)
        paths[name] = rel_path(slug, name)
    stats = {k: [round(float(v), 1) for v in arr.reshape(-1, 3).mean(0)]
             for k, arr in (("blendTerrain", blend_terrain), ("colorTerrain", color_terrain), ("clutter", clutter))}
    return {"frame": [round(v, 3) for v in fr], "res": n, "pixelM": round(px, 3), "paths": paths, "meanRGB": stats}
