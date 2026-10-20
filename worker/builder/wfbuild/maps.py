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
| ``<slug>_clutter_t`` | G grass 5-80 m from the road (stock fields13), R bushes, B flowers, alpha 128; thinned to the stock budget (``config.CLUTTER_MAX_*``) | terrain (slot 11) |
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


def clutter_density(mask: np.ndarray, size_m: float, window_m: float = C.CLUTTER_WINDOW_M) -> tuple[float, float]:
    """(hectares at full density, highest mean within a window_m square) of a clutter mask (n x n [x channels],
    0..1, channels summed) over a square frame of size_m. Also used by the export checks on the built .bmap."""
    m = mask.sum(axis=2) if mask.ndim == 3 else mask
    n = m.shape[0]
    px = size_m / n
    w = int(min(n, max(1, round(window_m / px))))
    c = np.pad(m.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    s = c[w:, w:] - c[:-w, w:] - c[w:, :-w] + c[:-w, :-w]
    return float(m.sum() * px * px / 1e4), float(s.max() / (w * w))


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
    # how deep inside the trees: 0 at a wood's edge, 1 well inside (the planner's seeded clusters, or an imported
    # track's real woods)
    canopy = planner.canopy_grid(n)
    if env_name == "parkland":
        # lush grass in the open; forest floor only well inside the woods, ragged at its edge
        b = smooth01((canopy - 0.35 + (n_fine - 0.5) * 0.5) / 0.3) * smooth01((d - 12.0) / 8.0)
    elif env_name == "forest":
        # forest floor only under the dense cluster cores (stock forest11 is mostly grass, mean blend G ~ 70%);
        # broken up by fine noise so the edge isn't a contour line
        b = smooth01((canopy - 0.55 + (n_fine - 0.5) * 0.5) / 0.3) * smooth01((d - 16.0) / 10.0)
    elif env_name == "farmland" and planner.parcels is not None:
        fld = planner.parcels.is_field(pts).reshape(n, n) & (planner.parcels.border(pts).reshape(n, n) > 3.0)
        b = fld * smooth01((d - 40.0) / 10.0)
    elif env_name == "coastal":
        # grey rock on the foreshore (just above the sea) and on steep outcrops, patchy at its upper edge
        b = smooth01((n_big - 0.78) / 0.08) * 0.5 * smooth01((d - 25.0) / 15.0)
        if planner.sea is not None:
            zg = geo.ground(pts).reshape(n, n)
            gy_, gx_ = np.gradient(zg, px)
            steep = smooth01((np.hypot(gx_, gy_) - 0.35) / 0.25)
            shore = smooth01((planner.sea + 3.0 + (n_fine - 0.5) * 2.5 - zg) / 1.5)
            b = np.maximum(b, np.maximum(shore, steep * smooth01((planner.sea + 14.0 - zg) / 4.0)))
            b = b * smooth01((d - 8.0) / 6.0)
    elif env_name == "airfield":
        # open short grass round the circuit (the old airfield); big patches of ploughed arable land well beyond
        b = smooth01((n_big - 0.5) / 0.1) * smooth01((d - 220.0) / 60.0)
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

    # ---- road blend: R main, G variant patches, B worn edges (asphalt: dust of the environment's gravel)
    rg = smooth01((n_mid - 0.55) / 0.12) * 0.8
    rb = smooth01((1.4 + d + (n_fine - 0.5) * 1.2) / 1.2) * 0.55   # dusty last ~1.3 m inside each edge
    rb = np.maximum(rb, smooth01((n_big - 0.7) / 0.1) * 0.35)
    # mixed tracks: the asphalt turns into the next sector's gravel over TRANSITION_RAMP before each switch
    ramp = np.zeros_like(d)
    for sw in geo.surface_switches():
        c, t = sw["point"][:2], sw["tangent"]
        rel = np.stack([gx - c[0], gy - c[1]], axis=2)
        along = (rel[:, :, 0] * t[0] + rel[:, :, 1] * t[1]) * sw["asphalt"]     # metres into the asphalt
        across = np.abs(-rel[:, :, 0] * t[1] + rel[:, :, 1] * t[0])
        near = smooth01((sw["hw"] + 4.0 - across) / 2.0)
        jag = (n_fine - 0.5) * 8.0 + (n_mid - 0.5) * 6.0
        ramp = np.maximum(ramp, near * smooth01((C.TRANSITION_RAMP - along + jag) / (0.6 * C.TRANSITION_RAMP))
                          * smooth01((along + 3.0) / 2.0))
    rb = np.maximum(rb, ramp)
    rg = rg * (1 - rb)
    rr_ = np.clip(1.0 - rg - rb, 0.0, 1.0)
    s = np.maximum(rr_ + rg + rb, 1e-6)
    blend_road = np.stack([rr_ / s, rg / s, rb / s], axis=2) * 255.0

    # ---- terrain colour (soft-light; 128 neutral)
    mean = np.array(e["colour"]["mean"], dtype=np.float64)
    std = float(e["colour"]["std"])
    col = np.empty((n, n, 3))
    # macro variation: large soft patches (stock colour maps span ~+-35 around their mean), a slow hue drift
    # between dry/yellow and lush/green, and finer mottling
    n_macro = nz("map-macro", 180.0, 3)
    var = (n_macro - 0.5) * 2.6 * std + (n_mid - 0.5) * 0.9 * std + (n_fine - 0.5) * 0.4 * std
    hue = (nz("map-hue", 400.0, 2) - 0.5) * 1.6 * std
    col[:, :, 0] = mean[0] + var + hue
    col[:, :, 1] = mean[1] + var * 1.05 - hue * 0.2
    col[:, :, 2] = mean[2] + var * 0.7 - hue * 0.9
    # grass reads greener than the dirt: lift G and drop B where the blend map is grass (soft light, 128 neutral)
    green = float(e["colour"].get("grassGreen", 8.0))
    col[:, :, 1] += g * green
    col[:, :, 2] -= g * green * 0.8
    # shade under the tree clusters (stock forest11's colour map darkens to ~60 there)
    shade = float(e["colour"].get("canopyShade", 14.0))
    col -= (canopy * shade)[:, :, None]
    # darker, browner wear along the road edges and where people and vehicles stand
    wear = smooth01((14.0 - dj) / 10.0) * float(e["colour"].get("wear", 16.0)) + zones * 10.0
    col[:, :, 0] -= wear * 0.8
    col[:, :, 1] -= wear
    col[:, :, 2] -= wear * 1.1
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

    def clutter_mask(hi: float) -> np.ndarray:
        band = smooth01((dj - lo) / 3.0) * smooth01((hi - dj) / 25.0)
        # a strong, continuous grass band next to the road (thinning towards its outer edge and under the
        # canopy), with fine gaps so it doesn't read as a stripe
        grass = band * (0.75 + 0.25 * smooth01((n_fine - 0.25) / 0.3)) * (1 - r) * (1 - b * 0.7) * (1 - canopy * 0.4)
        cap = smooth01((hi - dj) / 25.0) if hi < C.GRASS_BAND[1] else 1.0  # sparse patches further out
        grass = np.maximum(grass, 0.35 * smooth01((dj - lo) / 3.0) * cap * smooth01((n_mid - 0.55) / 0.1) * (1 - r) * (1 - b))
        bushes = band * smooth01((n_mid - 0.66) / 0.08) * (1 - r) * 0.6
        flowers = band * smooth01((n_fine - 0.72) / 0.1) * (1 - r) * 0.6
        if env_name == "coastal":
            # windswept: no grass blades on the rock, sparser flowers (the yellow gorse is placed as bushes)
            grass = grass * (1 - b)
            bushes = bushes * (1 - b) * 0.6
            flowers = flowers * (1 - b) * 0.5
        return np.stack([bushes, grass, flowers], axis=2)

    clutter = clutter_mask(hi)
    ha, local = clutter_density(clutter, size)
    # over the stock budget (a long lap, or several layouts): first pull the band's outer edge in (down to
    # CLUTTER_MIN_BAND_M), so the grass right beside the road stays dense, then thin what is left evenly
    while (ha > C.CLUTTER_MAX_HA or local > C.CLUTTER_MAX_LOCAL) and hi > C.CLUTTER_MIN_BAND_M:
        hi = max(C.CLUTTER_MIN_BAND_M, hi - 10.0)
        clutter = clutter_mask(hi)
        ha, local = clutter_density(clutter, size)
    # keep within the stock clutter budget (config.CLUTTER_MAX_*): thin the whole mask evenly
    clutter_scale = min(1.0, C.CLUTTER_MAX_HA / max(ha, 1e-9), C.CLUTTER_MAX_LOCAL / max(local, 1e-9))
    clutter = clutter * clutter_scale * 255.0
    clutter_a = np.full((n, n), float(e.get("clutterAlpha", 128)))

    out_dir = os.path.join(root, "data", "track", slug, "textures")
    paths = {}
    for name, rgb, a in (("blend_terrain_c", blend_terrain, None), ("blend_road_c", blend_road, None),
                         ("color_terrain_c", color_terrain, None), ("color_road_c", color_road, None),
                         ("clutter_t", clutter, clutter_a)):
        write_tga(os.path.join(out_dir, f"{slug}_{name}.tga"), rgb, a)
        paths[name] = rel_path(slug, name)
    # green track-limit paint (no stock plain-green road paint exists; prop_curb1_green is a kerb): worn
    # green-painted asphalt, tiling, 256 px per TEX_TILE["limits"] metres
    t = 256
    tile_noise = lambda what, feat: Noise2D(seed_for(slug, what), (0.0, 0.0, 1.0), feat, 2).grid(t)  # noqa: E731
    wear = tile_noise("limits-wear", 0.25)
    grain = np.random.default_rng(seed_for(slug, "limits-grain")).random((t, t))
    base = np.array([52.0, 118.0, 60.0])
    shade = 1.0 + (wear - 0.5) * 0.25 + (grain - 0.5) * 0.12
    asphalt = smooth01((wear - 0.78) / 0.1)[:, :, None]  # a few patches where the paint has worn to the asphalt
    limits = base[None, None, :] * shade[:, :, None] * (1 - asphalt) + 70.0 * asphalt * shade[:, :, None]
    write_tga(os.path.join(out_dir, f"{slug}_limits_c.tga"), limits)
    paths["limits_c"] = rel_path(slug, "limits_c")
    stats = {k: [round(float(v), 1) for v in arr.reshape(-1, 3).mean(0)]
             for k, arr in (("blendTerrain", blend_terrain), ("colorTerrain", color_terrain), ("clutter", clutter))}
    return {"frame": [round(v, 3) for v in fr], "res": n, "pixelM": round(px, 3), "paths": paths, "meanRGB": stats,
            "clutter": {"fullDensityHa": round(ha * clutter_scale, 2), "localMax": round(local * clutter_scale, 3),
                        "scale": round(clutter_scale, 3), "grassBandM": [lo, hi]}}
