"""Footprints of placed props (oriented rectangles from the asset table) and their overlaps: two props sharing a
footprint render coplanar faces that flicker (z-fighting; Brands Hatch's overlapping pit buildings and grandstand
tiles, Chris 2026-10-25). Used by pits.py while placing and by check_scene.py. Pure numpy."""
import math
import numpy as np

# small, organic or crowd props: overlapping them is harmless
SKIP = {"trees", "foliage", "bushes", "spectators", "staff", "flags", "grass", "rocks", "gorse", "shore"}
# structures spanning the road (their legs stand apart, nothing shares their faces)
SPANS = {"gantry", "bridges"}

# The real plan extents of stock props whose faces line up with others' (rows, tiles, panels, buildings): local
# (x0, x1, y0, y1) in metres at scale 1, measured from the stock meshes (data/art/objects/*.scne, scne_read,
# 2026-10-26). Several are off-centre, so a centred width x depth box misplaces their faces by up to 0.6 m.
EXTENTS = {
    "prop_barrier8": (-1.83, 1.87, -0.41, 0.41),
    "prop_concrete_element3": (-3.0, 3.0, -0.7, 0.71),
    "prop_adfence3": (-1.92, 1.92, 0.0, 0.01),
    "prop_adfence9": (-1.92, 1.92, 0.0, 0.01),
    "prop_tarmac2_pitstop_part2": (-14.52, 13.83, -6.19, 6.19),
    "prop_startbuilding_fields14": (-10.43, 11.40, -5.09, 4.17),
    "prop_start_building_forest11": (-11.34, 10.12, -7.46, 7.35),
    "prop_start_building_forest09": (-10.43, 10.49, -4.38, 4.47),
    "prop_commentatorbuilding_forest09": (-3.52, 3.34, -3.16, 3.17),
    "prop_tarmac2_tenthall": (-7.61, 7.61, -17.24, 17.80),
    "prop_start_grandstand1": (-12.50, 12.49, -3.55, 4.21),
    "prop_start_grandstand3g": (-23.73, 24.38, -6.77, 8.02),
    "prop_derby_grandstand01": (-16.25, 16.25, -2.86, 2.89),
    "prop_cameratower": (-2.92, 2.92, -3.50, 3.50),
    "prop_start_tent8": (-5.05, 5.05, -5.11, 5.14),
    "prop_start_tent4": (-10.67, 10.67, -11.07, 11.07),
    "prop_hightent_03": (-4.28, 4.26, -4.32, 4.31),
    "prop_trailer2": (-9.24, 9.24, -1.56, 1.56),
    "vehicle_t2_crewbus01": (-1.41, 1.41, -6.29, 6.29),
    "vehicle_t2_cargotruck01": (-1.42, 1.42, -5.31, 5.28),
}


def boxed(asset, info) -> bool:
    """A real box (measured, or a width in the asset table): its edges are the prop's faces."""
    return asset in EXTENTS or "width" in info


def rect(asset, x, y, yaw, scale, info):
    """The prop's plan rectangle (corners, counter-clockwise from local +x+y): the measured extents where known,
    otherwise the asset table's width x depth (or a 2r square) centred on the origin."""
    if asset in EXTENTS:
        x0, x1, y0, y1 = (v * scale for v in EXTENTS[asset])
    else:
        w = float(info.get("width", 2 * info["r"])) * scale
        d = float(info.get("depth", 2 * info["r"] if "width" not in info else 1.0)) * scale
        x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    X = np.array([math.cos(yaw), math.sin(yaw)])
    Y = np.array([-math.sin(yaw), math.cos(yaw)])
    c = np.array([x, y])
    return np.array([c + X * x1 + Y * y1, c + X * x0 + Y * y1, c + X * x0 + Y * y0, c + X * x1 + Y * y0])


def clip(subject, clipper):
    def inside(p, a, b):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0
    def inter(p, q, a, b):
        x1, y1, x2, y2 = *p, *q
        x3, y3, x4, y4 = *a, *b
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(den) < 1e-12:
            return q
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
        return np.array([x1 + t * (x2 - x1), y1 + t * (y2 - y1)])
    out = list(subject)
    for i in range(len(clipper)):
        a, b = clipper[i], clipper[(i + 1) % len(clipper)]
        inp, out = out, []
        if not inp:
            break
        s = inp[-1]
        for e in inp:
            if inside(e, a, b):
                if not inside(s, a, b):
                    out.append(inter(s, e, a, b))
                out.append(e)
            elif inside(s, a, b):
                out.append(inter(s, e, a, b))
            s = e
    return out


def area(poly):
    if len(poly) < 3:
        return 0.0
    p = np.array(poly)
    return 0.5 * abs(float(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1))))


def ccw(q):
    return q if area_signed(q) > 0 else q[::-1]


def area_signed(p):
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))


def overlaps(items, frac=0.15):
    """items: (label, category, rect). Pairs whose overlap exceeds frac of the smaller footprint."""
    R = [(lab, cat, ccw(q)) for lab, cat, q in items if cat not in SKIP]
    C = np.array([q.mean(0) for _, _, q in R]).reshape(-1, 2)
    rad = np.array([np.hypot(*(q - q.mean(0)).T).max() for _, _, q in R])
    out = []
    for i in range(len(R)):
        d = np.hypot(*(C - C[i]).T)
        for j in np.nonzero((d < rad + rad[i]) & (np.arange(len(R)) > i))[0]:
            a = area(clip(list(R[i][2]), R[j][2]))
            small = min(area(list(R[i][2])), area(list(R[j][2])))
            if small > 0 and a > frac * small:
                out.append((R[i][0], R[i][1], R[j][0], R[j][1], round(a / small, 2)))
    return out


def dist_to_poly(p, poly) -> float:
    """Distance (m) from point p to polygon ``poly`` (n x 2, either winding); 0 inside."""
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(poly, dtype=np.float64)
    a, b = q, np.roll(q, -1, axis=0)
    ab = b - a
    t = np.clip(np.einsum('ij,ij->i', p - a, ab) / np.maximum(np.einsum('ij,ij->i', ab, ab), 1e-12), 0.0, 1.0)
    d = float(np.hypot(*(a + ab * t[:, None] - p).T).min())
    inside = False
    for (x1, y1), (x2, y2) in zip(a, b):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            inside = not inside
    return 0.0 if inside else d


def boxed_items(props, info_of):
    """props: (asset, category, x, y, yaw, scale). The (label, category, rect) of the boxed ones that can share a
    face with another: not the small organic props, not the spans."""
    out = []
    for asset, cat, x, y, yaw, scale in props:
        if cat in SKIP or cat in SPANS:
            continue
        info = info_of(asset)
        if boxed(asset, info):
            out.append((asset, cat, rect(asset, x, y, yaw, scale, info)))
    return out


COPLANAR_OFF = 0.02                      # faces closer than this (m) and parallel share a plane
COPLANAR_COS = math.cos(math.radians(0.5))
COPLANAR_MIN = 0.01                       # overlap along the face (m) that counts


def coplanar(items, off: float = COPLANAR_OFF, min_len: float = COPLANAR_MIN):
    """items: (label, category, rect) of boxed props (``boxed``). Pairs of props with a vertical face each on one
    plane (edges parallel within 0.5 deg and under ``off`` apart) overlapping along it by more than ``min_len``:
    the two faces z-fight (Chris 2026-10-26: pit walls, grandstand boards, ad fences). Returns (label a, category
    a, label b, category b, overlap m, where)."""
    R = [(lab, cat, np.asarray(q, dtype=np.float64)) for lab, cat, q in items]
    if len(R) < 2:
        return []
    C = np.array([q.mean(0) for _, _, q in R])
    rad = np.array([np.hypot(*(q - q.mean(0)).T).max() for _, _, q in R])
    out = []
    for i in range(len(R)):
        d = np.hypot(*(C - C[i]).T)
        for j in np.nonzero((d < rad + rad[i] + off) & (np.arange(len(R)) > i))[0]:
            best = 0.0
            qa, qb = R[i][2], R[j][2]
            for ea in range(4):
                a0, a1 = qa[ea], qa[(ea + 1) % 4]
                la = float(np.hypot(*(a1 - a0)))
                if la < 1e-6:
                    continue
                u = (a1 - a0) / la
                for eb in range(4):
                    b0, b1 = qb[eb], qb[(eb + 1) % 4]
                    lb = float(np.hypot(*(b1 - b0)))
                    if lb < 1e-6:
                        continue
                    v = (b1 - b0) / lb
                    if abs(float(u @ v)) < COPLANAR_COS:
                        continue
                    if abs(u[0] * (b0[1] - a0[1]) - u[1] * (b0[0] - a0[0])) > off:
                        continue
                    t0, t1 = sorted((float(u @ (b0 - a0)), float(u @ (b1 - a0))))
                    best = max(best, min(la, t1) - max(0.0, t0))
            if best > min_len:
                out.append((R[i][0], R[i][1], R[j][0], R[j][1], round(best, 3), [round(float(v), 1) for v in C[i]]))
    return out
