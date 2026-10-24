"""Overlay plots for the pits survey: the nDSM (LIDAR heights above ground) under the committed sample's roads, the
OSM features around the pits and, once written, the pits file's lanes, walls, buildings, paddock and gantries."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from pits_common import LOCAL, Circuit
from pits_lidar import ndsm

KIND_STYLE = {
    "raceway": ("#d62728", 1.6), "service": ("#ff7f0e", 0.9), "building": ("#1f77b4", 0.8), "grandstand": ("#9467bd", 1.2),
    "barrier": ("#2ca02c", 0.8), "area": ("#8c564b", 0.6), "bridge": ("#e377c2", 2.4), "other": ("#7f7f7f", 0.5),
}


def classify(t: dict) -> str | None:
    if t.get("bridge") or t.get("man_made") == "bridge":
        return "bridge"
    if t.get("highway") == "raceway":
        return "raceway"
    if t.get("building") == "grandstand" or t.get("leisure") == "stadium":
        return "grandstand"
    if "building" in t:
        return "building"
    if t.get("barrier"):
        return "barrier"
    if t.get("highway") in ("service", "track", "unclassified", "residential", "footway", "path"):
        return "service"
    if t.get("amenity") == "parking" or t.get("landuse") or t.get("area:highway") or t.get("leisure"):
        return "area"
    return None


def road_polys(route: np.ndarray, w: np.ndarray, closed: bool) -> np.ndarray:
    p = np.vstack([route, route[:1]]) if closed else route
    ww = np.concatenate([w, w[:1]]) if closed else w
    t = np.gradient(p, axis=0)
    t /= np.linalg.norm(t, axis=1)[:, None]
    nl = np.column_stack([-t[:, 1], t[:, 0]])
    return np.vstack([p + nl * ww[:, None] / 2, (p - nl * ww[:, None] / 2)[::-1]])


def overlay(c: Circuit, centre: np.ndarray, half: float, out: Path, title: str, pits: dict | None = None,
            labels: bool = True, ndsm_max: float = 14.0) -> None:
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    lo, hi = centre - half, centre + half
    g0, g1 = c.grid_of_local(lo), c.grid_of_local(hi)
    fig, ax = plt.subplots(figsize=(12, 12), dpi=110)
    try:
        nd, _ = ndsm(c, g0[0], g0[1], g1[0], g1[1])
        ext_g = [nd.x0, nd.x0 + nd.z.shape[1], nd.y1 - nd.z.shape[0], nd.y1]
        e_lo, e_hi = c.local_of_grid([ext_g[0], ext_g[2]]), c.local_of_grid([ext_g[1], ext_g[3]])
        im = ax.imshow(np.clip(nd.z, -0.5, ndsm_max), extent=[e_lo[0], e_hi[0], e_lo[1], e_hi[1]], cmap="magma",
                       vmin=0, vmax=ndsm_max, interpolation="nearest", alpha=0.9)
        fig.colorbar(im, ax=ax, fraction=0.03, label="height above ground (m, LIDAR DSM - DTM)")
    except Exception as exc:  # no LIDAR here
        ax.set_facecolor("#222")
        title += f" (no nDSM: {exc})"
    for k, (route, w) in enumerate([(c.main, c.main_w)] + [(c.alts[a], c.alt_w[a]) for a in c.alts]):
        poly = road_polys(route, w, k == 0)
        ax.fill(poly[:, 0], poly[:, 1], color="#bbbbbb", alpha=0.35, lw=0)
        ax.plot(route[:, 0], route[:, 1], color="white", lw=0.6, ls="--")
    sel_ids = set()
    if pits:
        for key in ("osmWays",):
            sel_ids |= {int(v) for v in (pits.get("sources", {}).get(key) or [])}
    for wid, w in c.ways.items():
        t = w.get("tags", {})
        kind = classify(t)
        if not kind or not w.get("geometry"):
            continue
        g = c.geom(w)
        if len(g) < 2 or not ((g > lo - 50).all(1) & (g < hi + 50).all(1)).any():
            continue
        col, lw = KIND_STYLE[kind]
        closed = np.allclose(g[0], g[-1]) and len(g) > 3
        if closed and kind in ("building", "grandstand", "area"):
            ax.fill(g[:, 0], g[:, 1], facecolor=col, alpha=0.25 if kind != "area" else 0.08, edgecolor=col, lw=lw)
        else:
            ax.plot(g[:, 0], g[:, 1], color=col, lw=lw)
        if labels and kind in ("raceway", "building", "grandstand", "bridge", "area") and wid not in c.lap_ways:
            m = g.mean(0)
            if (m > lo).all() and (m < hi).all():
                nm = t.get("name") or t.get("building") or t.get("amenity") or t.get("landuse") or ""
                ax.text(m[0], m[1], f"{wid}\n{nm}"[:60], fontsize=5, color="cyan" if wid in sel_ids else "white")
    for nid, n in c.nodes.items():
        t = n.get("tags", {})
        if "raceway" in t or t.get("man_made") in ("bridge", "gantry", "tower"):
            p = c.local(n["lat"], n["lon"])
            if (p > lo).all() and (p < hi).all():
                ax.plot(*p, "y*", ms=8)
                ax.text(p[0], p[1], f" {t.get('raceway') or t.get('man_made')} {nid}", fontsize=6, color="yellow")
    if pits:
        draw_pits(ax, pits, c)
    # s ticks along the main lap
    for s in np.arange(0, c.L, 50):
        p, t = c.point_at(s)
        if (p > lo).all() and (p < hi).all():
            ax.text(p[0], p[1], f"{s:.0f}", fontsize=6, color="lime", ha="center")
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=9, clip_on=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def draw_pits(ax, pits: dict, c: Circuit) -> None:
    loc = lambda pts: c.local_of_grid(np.asarray(pts, dtype=float))  # noqa: E731
    for lane in pits.get("pitLanes", []):
        p = loc(lane["centreline"])
        ax.plot(p[:, 0], p[:, 1], color="#00e5ff", lw=2.0)
        ax.text(*p[len(p) // 2], " pit lane", color="#00e5ff", fontsize=8, clip_on=True)
    for wall in pits.get("pitWalls", []):
        p = loc(wall["line"])
        ax.plot(p[:, 0], p[:, 1], color="#76ff03", lw=2.5)
    for b in pits.get("buildings", []):
        p = loc(b["footprint"])
        p = np.vstack([p, p[:1]])
        ax.plot(p[:, 0], p[:, 1], color="#ffea00", lw=1.5)
        ax.text(*p[:-1].mean(0), f"{b.get('name', b['kind'])}\n{b.get('heightM', '?')} m", color="#ffea00", fontsize=6, clip_on=True)
    for a in pits.get("paddocks", []):
        p = loc(a["polygon"])
        p = np.vstack([p, p[:1]])
        ax.plot(p[:, 0], p[:, 1], color="#ff4081", lw=1.2, ls=":")
    for g in pits.get("gantries", []):
        p = loc([g["at"]])[0]
        ax.plot(*p, "o", color="#ff1744", ms=7)
        ax.text(p[0], p[1], f"  {g['kind']} {g.get('name', '')} s={g.get('sM', '?')}", color="#ff1744", fontsize=7, clip_on=True)
    for g in pits.get("grandstands", []):
        p = loc(g["footprint"])
        p = np.vstack([p, p[:1]])
        ax.plot(p[:, 0], p[:, 1], color="#d500f9", lw=1.5)
