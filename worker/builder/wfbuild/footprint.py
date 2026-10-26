"""Footprints of placed props (oriented rectangles from the asset table) and their overlaps: two props sharing a
footprint render coplanar faces that flicker (z-fighting; Brands Hatch's overlapping pit buildings and grandstand
tiles, Chris 2026-10-25). Used by pits.py while placing and by check_scene.py. Pure numpy."""
import math
import numpy as np

# small, organic or crowd props: overlapping them is harmless
SKIP = {"trees", "foliage", "bushes", "spectators", "staff", "flags", "grass", "rocks", "gorse", "shore"}


def rect(asset, x, y, yaw, scale, info):
    w = float(info.get("width", 2 * info["r"])) * scale
    d = float(info.get("depth", 2 * info["r"] if "width" not in info else 1.0)) * scale
    X = np.array([math.cos(yaw), math.sin(yaw)])
    Y = np.array([-math.sin(yaw), math.cos(yaw)])
    c = np.array([x, y])
    return np.array([c + X * w / 2 + Y * d / 2, c - X * w / 2 + Y * d / 2, c - X * w / 2 - Y * d / 2, c + X * w / 2 - Y * d / 2])


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
