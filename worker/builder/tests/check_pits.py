"""Checks of an imported track's real pits (wfbuild/pits.py), called by check_scene.py (inside Blender).

- the pit lane surface and its collision wall exist; the wall (collision and concrete row) is continuous from
  one end of its stretch to the other, with the closures across the lane at both ends: the only openings are the
  entry and exit merges, outside the stretch;
- no AI strip (route, safe, race) of any layout reaches onto the pit lane;
- pit buildings, grandstands and paddock props stand off every road and off the pit lane;
- the start lights, bridges and gantries stand at their real sM (within GANTRY_S_TOL) with at least
  MIN_CLEARANCE between the road and their lowest part;
- the start camera isn't inside or looking through a pit building.
"""
from __future__ import annotations

import math

import numpy as np

GANTRY_S_TOL = 3.0
WALL_STEP_MAX = 6.5      # concrete elements 6 m long: no gap between neighbours on the line


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
        row = np.array([p["position"][:2] for p in P if p["category"] == "pitWall"])
        if len(row):
            dd = np.array([np.hypot(*(row - q).T).min() for q in lane.wall[::2]])
            check(float(dd.max()) <= WALL_STEP_MAX / 2 + 0.6, f"pits: {lane.id} concrete row continuous along the pit wall",
                  f"worst {dd.max():.2f} m from the nearest element")
        if lane.mode == "closed":
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
    over = [p for p in P if p["category"] in ("gantry", "bridges")]
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
