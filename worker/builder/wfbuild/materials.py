"""Multi-slot stock-style materials per environment (pure Python): #blend road / verge / terrain, the road-edge
#decal, and the backdrop (#horizon, #noshadow far ground, #xtree tree cards).

A spec is ``{"name": "name#tag#tag", "slots": [(slot, "data/....tga", uv, (sx, sy)), ...]}``: texture slot
0-11, UV channel 1 or 2 and the tiling scale Toolbox writes from a Mapping node. The 12-slot #blend layout is the
stock one (worker/docs/visual-assets.md): 0/2/4 albedo R/G/B, 1/3/5 their _ns, 6 blend map (UV2), 7 detail,
8 macro _ns (UV2), 9 colour map (UV2), 10 clutter colour, 11 clutter mask (UV2).
"""
from __future__ import annotations

from . import config as C
from . import envs

TGA = ".tga"


def _t(stem: str) -> str:
    return envs.stem(stem) + TGA


def _layers(layers: list[dict], uv_tile: float) -> list[tuple]:
    out = []
    for k, lay in enumerate(layers):
        tx, ty = lay["tile"]
        sc = (round(uv_tile / tx, 4), round(uv_tile / ty, 4))
        out.append((2 * k, _t(lay["c"]), 1, sc))
        out.append((2 * k + 1, envs.ns_of(lay) + TGA, 1, sc))
    return out


def build(env_name: str | None, slug: str, maps: dict, n_cards: int, generated: dict | None = None) -> dict[str, dict]:
    """Material specs keyed like the builder's mesh material keys. ``generated`` holds the backdrop's own
    textures (data paths without extension): "horizon" (a painted skyline), "sea" and "lake"."""
    generated = generated or {}
    name = envs.env_name(env_name)
    e = envs.env(name)
    mp = {k: v + TGA for k, v in maps["paths"].items()}
    detail = ("data/art/textures/detailblend_white_c" + TGA)
    macro = _t(e["macro"])
    clutter = e.get("clutterGroup")
    ground = [e["ground"][k] for k in ("R", "G", "B")]
    terrain_slots = _layers(ground, C.TEX_TILE["ground"]) + [
        (6, mp["blend_terrain_c"], 2, (1.0, 1.0)), (7, detail, 2, (1.0, 1.0)), (8, macro, 2, (1.0, 1.0)),
        (9, mp["color_terrain_c"], 2, (1.0, 1.0)),
        (10, "data/art/textures/default_clutter_colorgray_t" + TGA, 2, (1.0, 1.0)),
        (11, mp["clutter_t"], 2, (1.0, 1.0))]
    specs: dict[str, dict] = {
        "terrain": {"name": f"terrain_{name}#{e['terrainSurface']}" + (f"#{clutter}" if clutter else "") + "#blend",
                    "slots": terrain_slots},
        "shoulder": {"name": f"verge_{name}#gravel#blend", "slots": terrain_slots},
    }
    for surface, physics in (("tarmac", "asphalt"), ("gravel", "gravel"), ("sand", "gravel")):
        layers = list(e["roads"][surface])
        if surface == "tarmac":
            # B = the environment's gravel, as stock asphalt (forest11: sand pebbles): dust on the worn edges, and
            # on mixed tracks the ramp into the next gravel sector (maps.py) ends in the gravel road's own texture
            layers = layers[:2] + [e["roads"]["gravel"][0]]
        specs[f"road_{surface}"] = {
            "name": f"road_{surface}_{name}#{physics}#blend",
            "slots": _layers(layers, C.TEX_TILE["road"]) + [
                (6, mp["blend_road_c"], 2, (1.0, 1.0)), (7, detail, 2, (1.0, 1.0)), (8, macro, 2, (1.0, 1.0)),
                (9, mp["color_road_c"], 2, (1.0, 1.0))]}
    if "limits_c" in mp:
        # green track-limit strips: grippy but slow (stock surface "slowdown"), our own paint texture
        specs["limits"] = {"name": "track_limits#slowdown",
                           "slots": [(1, mp["limits_c"], 1, (1.0, 1.0)),
                                     (9, "data/art/textures/default_zero_spec_s" + TGA, 1, (1.0, 1.0))]}
    d = e["edgeDecal"]
    edge = [(1, _t(d["c"]), 1, (1.0, 1.0)), (6, _t(d["c"]), 1, (1.0, 1.0))]
    if d.get("n"):
        edge.append((8, _t(d["n"]), 1, (1.0, 1.0)))
    if d.get("s"):
        edge.append((9, _t(d["s"]), 1, (1.0, 1.0)))
    specs["edge_decal"] = {"name": f"road_edge_{name}#decal#priority1", "slots": edge}
    td = envs.table()["transitionDecal"]
    specs["transition_decal"] = {"name": f"road_transition_{name}#decal#priority2",
                                 "slots": [(1, _t(td["c"]), 1, (1.0, 1.0)), (6, _t(td["c"]), 1, (1.0, 1.0)),
                                           (7, _t(td["ao"]), 1, (1.0, 1.0)), (8, _t(td["n"]), 1, (1.0, 1.0)),
                                           (9, _t(td["s"]), 1, (1.0, 1.0))]}
    h = e["horizon"]
    hc = generated["horizon"] + TGA if generated.get("horizon") else _t(h["c"])
    specs["horizon"] = {"name": f"horizon_{name}#horizon",
                        "slots": [(1, hc, 1, (1.0, 1.0)), (6, hc, 1, (1.0, 1.0)),
                                  (7, _t(h["fog"]), 1, (1.0, 1.0))]}
    if generated.get("sea"):
        specs["sea"] = {"name": f"sea_{name}#noshadow",
                        "slots": [(1, generated["sea"] + TGA, 1, (1.0, 1.0)),
                                  (9, "data/art/textures/default_zero_spec_s" + TGA, 1, (1.0, 1.0))]}
    if generated.get("lake"):
        specs["lake"] = {"name": f"lake_{name}#noshadow",
                         "slots": [(1, generated["lake"] + TGA, 1, (1.0, 1.0)),
                                   (9, "data/art/textures/default_zero_spec_s" + TGA, 1, (1.0, 1.0))]}
    specs["far_ground"] = {"name": f"far_ground_{name}#noshadow",
                           "slots": [(1, _t(e["farGround"]), 1, (1.0, 1.0)),
                                     (9, "data/art/textures/default_zero_spec_s" + TGA, 1, (1.0, 1.0))]}
    cards = list(e.get("treeCards", [])) + list(e.get("skylineCards", []))
    for k, c in enumerate(cards[:max(n_cards, 0)] if n_cards else cards):
        specs[f"card_{k}"] = {"name": f"backdrop_card{k}_{name}#xtree",
                              "slots": [(1, _t(c), 1, (1.0, 1.0)),
                                        (9, "data/art/textures/default_foliage_s" + TGA, 1, (1.0, 1.0))]}
    return specs


def texture_paths(specs: dict[str, dict]) -> set[str]:
    return {p for s in specs.values() for (_slot, p, _uv, _sc) in s["slots"]}
