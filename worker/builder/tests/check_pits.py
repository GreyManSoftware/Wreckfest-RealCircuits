"""Checks of an imported track's real pits (wfbuild/pits.py), called by check_scene.py (inside Blender).

- the pit lane surface and its collision wall exist; the wall (collision and concrete row) is continuous from
  one end of its stretch to the other, with the closures across the lane at both ends: the only openings are the
  entry and exit merges, outside the stretch; it doesn't wrap round the corners or junction noses at its ends;
- no AI strip (route, safe, race) of any layout reaches onto the pit lane;
- pit buildings, grandstands and paddock props stand off every road and off the pit lane;
- the start lights, bridges and gantries stand at their real sM (within GANTRY_S_TOL) with at least
  MIN_CLEARANCE between the road and their lowest part, every pillar off the roads (nearer than PART_CLEAR: reported,
  the real spans are approved);
- span banners on every driven face, within the span's footprint and MIN_CLEARANCE over the road, the welcome banner
  on the span nearest each layout's start (check_banners);
- the start camera isn't inside or looking through a pit building.
"""
from __future__ import annotations

import math

import numpy as np

GANTRY_S_TOL = 3.0
WALL_STEP_MAX = 6.5      # concrete elements 6 m long: no gap between neighbours on the line
WALL_MAX_TURN_DEG = 90.0  # the pit wall's heading range (smoothed over +-6 m): measured walls 3-74 deg


def _rect(p: dict, info: dict) -> np.ndarray:
    w = float(info.get("width", 2 * info["r"])) * p["scale"]
    d = float(info.get("depth", 2 * info["r"] if "width" not in info else 1.0)) * p["scale"]
    yaw = p["yaw"]
    X = np.array([math.cos(yaw), math.sin(yaw)])
    Y = np.array([-math.sin(yaw), math.cos(yaw)])
    c = np.array(p["position"][:2])
    return np.array([c + X * w / 2 + Y * d / 2, c - X * w / 2 + Y * d / 2, c - X * w / 2 - Y * d / 2,
                     c + X * w / 2 - Y * d / 2])


def check_pits_base(check, geo, sc: dict, obs: dict, manifest: dict, to_scene, world_verts) -> None:
    from wfbuild import envs, pits as pits_mod
    from wfbuild.fields import exact_edge_distance

    pits = geo.pits
    if pits is None:
        return
    P = sc["placements"]
    for lane in pits.lanes:
        built = lane.b1 > lane.b0
        check(built and f"pitlane_{lane.id}" in obs and f"walls_pit_{lane.id}" in obs,
              f"pits: pit lane {lane.id} surface and collision wall exist", f"{(lane.b1 - lane.b0) * pits_mod.LANE_CELL:.0f} m")
        if not built:
            continue
        # the collision wall follows the whole wall line (vertices every metre)
        V = world_verts(obs[f"walls_pit_{lane.id}"])[:, :2]   # scene coordinates
        line = to_scene(lane.wall, manifest)
        d = np.array([np.hypot(*(V - q).T).min() for q in line[::2]])
        check(float(d.max()) < 1.5, f"pits: {lane.id} collision wall continuous along the pit wall",
              f"{len(line)} m of wall, worst {d.max():.2f} m from the collision mesh")
        # one run along the pit straight, not round the corners at its ends (Anglesey 2026-10-26: the derived wall
        # wrapped the corner and the junction nose before the straight, 177 deg; tools/real_tracks_data/pits.py
        # trim_wall keeps it where the evidence puts it)
        if lane.wall is not None and len(lane.wall) > 2:
            ss = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(lane.wall, axis=0).T))])
            lo = np.searchsorted(ss, ss - 6.0)
            hi = np.minimum(np.searchsorted(ss, ss + 6.0, side="right") - 1, len(ss) - 1)
            dv = lane.wall[hi] - lane.wall[lo]
            h = np.unwrap(np.arctan2(dv[:, 1], dv[:, 0]))
            turn = math.degrees(float(h.max() - h.min()))
            check(turn <= WALL_MAX_TURN_DEG, f"pits: {lane.id} pit wall doesn't wrap round a corner or junction nose",
                  f"heading range {turn:.0f} deg over {ss[-1]:.0f} m (max {WALL_MAX_TURN_DEG:.0f})")
        row = np.array([p["position"][:2] for p in P if p["category"] == "pitWall"])
        if len(row):
            dd = np.array([np.hypot(*(row - q).T).min() for q in lane.wall[::2]])
            check(float(dd.max()) <= WALL_STEP_MAX / 2 + 0.6, f"pits: {lane.id} concrete row continuous along the pit wall",
                  f"worst {dd.max():.2f} m from the nearest element")
        from wfbuild import pits as PITS  # noqa: PLC0415
        if PITS.OPEN_LANES:
            check(not lane.closures(), f"pits: {lane.id} open at both ends (no barrier across the lane or a junction)")
            cps = [c for c in geo.model["checkpoints"] if c.get("routeIndex", 0) == 0] if hasattr(geo, "model") else []
            raw = getattr(lane, "d", None) or {}
            a, b = raw.get("entrySM"), raw.get("exitSM")
            if cps and a is not None and b is not None:
                L = geo.main.length
                span = (b - a) % L
                ins = [c for c in cps if 10.0 < (c["s"] - a) % L < span - 10.0]
                check(span <= 30.0 or bool(ins), f"pits: {lane.id} a checkpoint on the bypassed stretch (no pit short cut)")
        elif lane.mode == "closed":
            cl = lane.closures()
            check(len(cl) == 2, f"pits: {lane.id} closed at both ends (barriers across the lane where the wall ends)")
        # the wall plan: no track barrier on the pit side over the wall's stretch, AI margin in front of the wall
        plan = geo.wall_plan()[(geo.main.index, lane.side)]
        m = geo.main
        s0, s1 = lane.wall_s
        on = ((m.s - s0) % m.length) <= ((s1 - s0) % m.length)
        reach = m.w / 2.0 + plan["ai_margin"]
        lane_near = np.array([np.hypot(*(lane.p[lane.b0:lane.b1 + 1] - m.p[i, :2]).T).min() for i in np.nonzero(on)[0]])
        bad = int(np.sum(reach[on] > lane_near - lane.w / 2.0 + 0.05))
        check(bad == 0, f"pits: {lane.id} the AI route's run-off stops in front of the pit lane", f"{bad} samples reach it")
    # buildings, grandstands, paddock props: off the roads and off the lane
    sel = [p for p in P if p["category"] in ("pitBuildings", "pitPaddock", "realGrandstand")]
    worst, on_lane = np.inf, []
    lane_polys = [ln.polygon() for ln in pits.lanes if ln.b1 > ln.b0]
    for p in sel:
        q = _rect(p, envs.asset_info(p["asset"]))
        e = float(exact_edge_distance(geo, np.vstack([q, q.mean(0, keepdims=True)])).min())
        worst = min(worst, e)
        for poly in lane_polys:
            if pits_mod.point_in_poly(np.vstack([q, q.mean(0, keepdims=True)]), poly).any():
                on_lane.append(p["asset"])
    check(not sel or worst >= 1.0, "pits: buildings, grandstands and paddock props stand off every road",
          f"{len(sel)} props, nearest {worst:.1f} m from a road edge")
    check(not on_lane, "pits: nothing stands on the pit lane", ", ".join(on_lane[:3]))
    # measured run-off (runoff.py): no gravel trap under the pit lane or a pit building
    ro = getattr(geo, "runoff", None)
    if ro is not None and ro.active:
        lane_pts = [ln.p[ln.b0:ln.b1 + 1] for ln in pits.lanes if ln.b1 > ln.b0]
        on_trap = sum(int((ro.at(p) == 1).sum()) for p in lane_pts)
        bld = [_rect(p, envs.asset_info(p["asset"])).mean(0) for p in sel if p["category"] != "pitPaddock"]
        b_trap = int((ro.at(np.array(bld)) == 1).sum()) if bld else 0
        check(on_trap == 0 and b_trap == 0, "pits: no gravel trap under the pit lane or the pit buildings",
              f"{on_trap} lane samples, {b_trap} buildings on a trap")
    # gantries and bridges: real sM, clearance
    m = geo.main
    over = [p for p in P if p["category"] in ("gantry", "bridges") and not p["asset"].startswith("sponsor_")]
    for g in pits.gantries:
        r = m if g.get("route", "main") == "main" else geo.by_id.get(g["route"])
        want = float(g["sM"])
        best = None
        for p in over:
            i = int(np.argmin(np.hypot(*(r.p[:, :2] - np.array(p["position"][:2])).T)))
            ds = float(r.s_dist(r.s[i], want))
            if best is None or ds < best[0]:
                best = (ds, p, i)
        ok = best is not None and best[0] <= GANTRY_S_TOL + (12.0 if g["kind"] == "startLights" else 0.0)
        check(ok, f"pits: {g['kind']} {g.get('name', '')} at its real position (s {want:.0f} m)",
              f"{best[0]:.1f} m along" if best else "missing")
    # the real spans are approved (Chris 2026-10-26): their pillars / posts must stand off every road; pillars on the
    # verge and stairs or bases nearer than PART_CLEAR are reported (pits.py warns), not failed
    spans = [p for p in P if p["category"] == "bridges" and pits_mod.is_span(p["asset"])]
    on_road, near = [], []
    for p in spans:
        legs = float(exact_edge_distance(geo, pits_mod.span_parts(p["asset"], p["position"][:2], p["yaw"], p["scale"],
                                                                  legs_only=True)).min())
        parts = float(exact_edge_distance(geo, pits_mod.span_parts(p["asset"], p["position"][:2], p["yaw"],
                                                                   p["scale"])).min())
        if legs < pits_mod.LEG_MIN - 0.3:
            on_road.append(f"{p['asset']} {legs:.1f} m")
        if parts < pits_mod.PART_CLEAR - 0.3 or legs < pits_mod.LEG_CLEAR - 0.3:
            near.append(f"{p['asset']} pillars {legs:.1f} m, parts {parts:.1f} m")
    check(not on_road, "pits: every bridge pillar and gantry post stands off the roads", ", ".join(on_road[:3]))
    if near:
        print(f"INFO pits: spans kept nearer than the preferred {pits_mod.PART_CLEAR:.1f} m (approved real spans): "
              + "; ".join(near))
    check_banners(check, geo, P, spans)
    for rep in sc["info"].get("pits", {}).get("gantries", []):
        check(rep["clearanceM"] >= pits_mod.MIN_CLEARANCE - 0.05 and not rep["problems"],
              f"pits: {rep['kind']} {rep['name']} clears the road by at least {pits_mod.MIN_CLEARANCE:.0f} m, legs off the pits",
              f"{rep['clearanceM']:.2f} m {rep['problems']}")
    # the start camera: not inside a pit building, not looking through one
    rects = [(_rect(p, envs.asset_info(p["asset"])), p["position"][2] + envs.asset_info(p["asset"]).get("h", 5.0) * p["scale"])
             for p in sel if p["category"] != "pitPaddock"]
    for lay in manifest["layouts"]:
        cam = lay.get("startCamera")
        if not cam:
            continue
        eye, tgt = np.array(cam["position"]), np.array(cam["target"])
        blocked = []
        for q, top in rects:
            qs = to_scene(q, manifest)
            if pits_mod.point_in_poly(eye[None, :2], qs)[0]:
                blocked.append("inside")
                continue
            for t in np.linspace(0.05, 0.95, 30):
                pt = eye + (tgt - eye) * t
                if pt[2] < top and pits_mod.point_in_poly(pt[None, :2], qs)[0]:
                    blocked.append("through")
                    break
        check(not blocked, f"pits: {lay['name']} start camera has a clear view (not in or through a pit building)",
              ", ".join(blocked[:3]))


def check_banners(check, geo, P: list, spans: list) -> None:
    """Span banners (pits._banners, sponsor.py span / welcome panels): every span face a layout drives towards
    carries one (pits.driven_faces); each banner lies within its span's footprint (between the pillars, flush on a
    face, printed side outwards, from the span's lowest part up to the top of the face) and at least MIN_CLEARANCE
    over every road under it; every layout that drives under a span has the welcome banner on the face it sees of
    the span nearest its start line."""
    from wfbuild import pits as pits_mod, sponsor

    if not spans or not (sponsor.span_rotation() or sponsor.welcome()):
        return
    banners = [p for p in P if sponsor.is_banner(p["asset"])]
    on_face: dict[tuple[int, int], list[str]] = {}
    outside, low = [], []
    for b in banners:
        q = np.array(b["position"][:2])
        h = float(b["scale"])
        w = sponsor.banner_width(b["asset"], h)
        # every road under the panel (its width + 0.5 m, 3 m either side): the panel's lower edge over the highest
        BX, BY = pits_mod._axes(b["yaw"])
        tops = []
        for r in geo.routes:
            rel = r.p[:, :2] - q
            sel = (np.abs(rel @ BX) <= w / 2.0 + 0.5) & (np.abs(rel @ BY) <= 3.0)
            if sel.any():
                tops.append(float(max(r.p[sel, 2].max(), r.left[sel, 2].max(), r.right[sel, 2].max())))
        if tops and b["position"][2] - max(tops) < pits_mod.MIN_CLEARANCE - 0.05:
            low.append(f"{b['asset']} {b['position'][2] - max(tops):.2f} m")
        home = None
        for k, sp in enumerate(spans):
            F, s = pits_mod.FACES[sp["asset"]], sp["scale"]
            X, Y = pits_mod._axes(sp["yaw"])
            rel = q - np.array(sp["position"][:2])
            u, v = float(rel @ X), float(rel @ Y)
            if abs(u) <= F["half"] * s + 0.1 and F["neg"] * s - 0.4 <= v <= F["pos"] * s + 0.4:
                home = (k, sp, F, s, X, Y, u, v)
                break
        if home is None:
            outside.append(f"{b['asset']} on no span")
            continue
        k, sp, F, s, X, Y, u, v = home
        face = -1 if v < (F["neg"] + F["pos"]) / 2.0 * s else 1
        out_dir = np.array([math.sin(b["yaw"]), -math.cos(b["yaw"])])  # the banner's printed side (local -Y)
        z0 = float(sp["position"][2])
        bad = []
        if abs(u) + w / 2.0 > F["half"] * s + 0.05:
            bad.append(f"ends {abs(u) + w / 2.0:.1f} m out of {F['half'] * s:.1f}")
        if not (b["position"][2] >= z0 + F["low"] * s - 0.01 and b["position"][2] + h <= z0 + F["top"] * s + 0.01):
            bad.append("above or below the face")
        if float(out_dir @ Y) * face < 0.95:
            bad.append("printed side not outwards")
        if bad:
            outside.append(f"{b['asset']}: {', '.join(bad)}")
            continue
        on_face.setdefault((k, face), []).append(b["asset"])
    check(not outside, "pits: span banners hang on their span's faces (between the pillars, printed side out)",
          "; ".join(outside[:3]))
    check(not low, f"pits: span banners clear the road by at least {pits_mod.MIN_CLEARANCE:.0f} m", ", ".join(low[:3]))
    missing, near = [], {}
    for k, sp in enumerate(spans):
        df = pits_mod.driven_faces(geo, sp["asset"], sp["position"][:2], sp["yaw"], sp["scale"])
        for f, passes in df.items():
            if passes and not on_face.get((k, f)):
                missing.append(f"{sp['asset']} #{k} {'-y' if f < 0 else '+y'} ({passes[0]['layout']})")
            for ps in passes:
                if ps["layout"] not in near or ps["fromLineM"] < near[ps["layout"]][0]:
                    near[ps["layout"]] = (ps["fromLineM"], (k, f))
    check(not missing, "pits: every span face a layout drives towards carries a banner", ", ".join(missing[:3]))
    if sponsor.welcome():
        nowel = [f"{lay} ({d:.0f} m from its line)" for lay, (d, kf) in near.items()
                 if "sponsor_welcome" not in on_face.get(kf, [])]
        check(bool(near) and not nowel, "pits: the welcome banner faces each layout on the span nearest its start",
              ", ".join(nowel) if near else "no layout drives under a span")
    per = {f"#{k}{'-y' if f < 0 else '+y'}": [sponsor.sponsor_of(a) or "welcome" for a in v] for (k, f), v in on_face.items()}
    print(f"INFO pits: span banners {per}")


def check_pits_layout(check, geo, name: str, obs: dict, manifest: dict, to_scene, world_verts) -> None:
    from wfbuild import pits as pits_mod

    pits = geo.pits
    if pits is None:
        return
    polys = [to_scene(ln.polygon(), manifest) for ln in pits.lanes if ln.b1 > ln.b0]
    inside = []
    for k in ("route", "safe", "race"):
        for o in [o for n, o in obs.items() if n.startswith(f"#ai_{k}_")]:
            V = world_verts(o)[:, :2]
            for poly in polys:
                n_in = int(pits_mod.point_in_poly(V, poly).sum())
                if n_in:
                    inside.append(f"{o.name}: {n_in}")
    check(not inside, f"{name}: no AI strip reaches onto the pit lane", ", ".join(inside[:3]))
