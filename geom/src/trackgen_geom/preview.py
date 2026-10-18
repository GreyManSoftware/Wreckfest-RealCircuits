"""Deterministic SVG previews of a TrackModel: a top-down map and an elevation profile."""

from __future__ import annotations

import math
from html import escape

from . import constants as C
from .model import Route, TrackModel

ROUTE_COLOURS = ["#1f6fd1", "#d1491f", "#2a9d3a", "#9b38c7", "#c79b1f", "#1fa9c7"]


def _f(v: float) -> str:
    out = f"{v:.1f}"
    return "0.0" if out == "-0.0" else out


class _View:
    def __init__(self, model: TrackModel, size: int, pad_px: float) -> None:
        b = model.bounds
        margin = model.width_m
        self.min_x, self.max_y = b.min_x - margin, b.max_y + margin
        span_x, span_y = b.max_x - b.min_x + 2 * margin, b.max_y - b.min_y + 2 * margin
        self.scale = (size - 2 * pad_px) / max(span_x, span_y, 1.0)
        self.pad = pad_px
        self.width = round(span_x * self.scale + 2 * pad_px)
        self.height = round(span_y * self.scale + 2 * pad_px) + 30

    def pt(self, x: float, y: float) -> str:
        return f"{_f((x - self.min_x) * self.scale + self.pad)},{_f((self.max_y - y) * self.scale + self.pad + 30)}"

    def pts(self, coords: list[tuple[float, float]]) -> str:
        return " ".join(self.pt(x, y) for x, y in coords)


def _route_xy(route: Route) -> list[tuple[float, float]]:
    return [(s.x, s.y) for s in route.samples]


def _section(route: Route, s0: float, s1: float) -> list[tuple[float, float]]:
    """Samples with s0 <= s <= s1 (wrapping on closed routes), in driving order."""
    pick = []
    for smp in route.samples:
        d = smp.s - s0
        if route.closed:
            d %= route.length_m
        if 0 <= d <= s1 - s0:
            pick.append((d, smp.x, smp.y))
    pick.sort()
    return [(x, y) for _, x, y in pick]


def _across(x: float, y: float, h: float, half: float) -> tuple[tuple[float, float], tuple[float, float]]:
    nx, ny = -math.sin(h), math.cos(h)
    return (x + nx * half, y + ny * half), (x - nx * half, y - ny * half)


def preview_svg(model: TrackModel) -> str:
    v = _View(model, C.PREVIEW_SIZE_PX, 20.0)
    w = model.width_m * v.scale
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{v.width}" height="{v.height}" '
        f'viewBox="0 0 {v.width} {v.height}" font-family="sans-serif" font-size="12">',
        f'<rect width="{v.width}" height="{v.height}" fill="#5b7f45"/>',
        f'<text x="10" y="20" fill="#ffffff" font-size="14">{escape(model.name)} ({escape(model.slug)}): '
        f"lap {model.lap_length_m / 1000:.2f} km, width {model.width_m:g} m, {escape(model.elevation)}"
        f"{', reverse' if model.reverse else ''}</text>",
    ]
    by_id = {r.id: r for r in model.routes}
    ordered = model.routes[1:] + model.routes[:1]
    for layer, colour, extra in (("casing", "#2b2b2b", 3.0), ("road", None, 0.0)):
        for r in ordered:
            tag = "polygon" if r.closed else "polyline"
            fill = colour or ("#8a8a8a" if r.ai_index == 0 else "#a08a6a")
            out.append(
                f'<{tag} class="{layer}" data-route="{escape(r.id)}" points="{v.pts(_route_xy(r))}" fill="none" '
                f'stroke="{fill}" stroke-width="{_f(w + extra)}" stroke-linejoin="round" stroke-linecap="round"/>'
            )
    for c in model.crossings:
        if c.over_branch is None or not c.detected:
            continue
        b = c.branches[c.over_branch]
        r = by_id[b.route]
        sec = _section(r, b.s - c.deck_half_length_m - c.ramp_up_m, b.s + c.deck_half_length_m + c.ramp_down_m)
        if len(sec) >= 2:
            out.append(
                f'<polyline class="bridge" points="{v.pts(sec)}" fill="none" stroke="#1b1b1b" '
                f'stroke-width="{_f(w + 5)}" stroke-linejoin="round"/>'
            )
            out.append(
                f'<polyline class="bridge" points="{v.pts(sec)}" fill="none" stroke="#b9ad93" '
                f'stroke-width="{_f(w)}" stroke-linejoin="round"/>'
            )
    for r in ordered:
        tag = "polygon" if r.closed else "polyline"
        out.append(
            f'<{tag} points="{v.pts(_route_xy(r))}" fill="none" stroke="#ffffff" stroke-width="0.8" '
            f'stroke-dasharray="4 4" opacity="0.7"/>'
        )
    for cp in model.checkpoints:
        a, b = _across(cp.position[0], cp.position[1], cp.heading, cp.width_m / 2)
        colour = "#ffffff" if cp.is_finish else "#3fc1ff"
        width = "4" if cp.is_finish else "1.5"
        out.append(f'<polyline class="checkpoint" points="{v.pts([a, b])}" stroke="{colour}" stroke-width="{width}"/>')
        if cp.is_finish:
            out.append(f'<polyline points="{v.pts([a, b])}" stroke="#000000" stroke-width="4" stroke-dasharray="3 3"/>')
    for slot in model.grid.slots:
        x, y, h = slot.position[0], slot.position[1], slot.heading
        cx, cy = math.cos(h), math.sin(h)
        nx, ny = -cy, cx
        hl, hw = C.PREVIEW_CAR_LENGTH_M / 2, C.PREVIEW_CAR_WIDTH_M / 2
        corners = [
            (x + cx * hl + nx * hw, y + cy * hl + ny * hw),
            (x + cx * hl - nx * hw, y + cy * hl - ny * hw),
            (x - cx * hl - nx * hw, y - cy * hl - ny * hw),
            (x - cx * hl + nx * hw, y - cy * hl + ny * hw),
        ]
        fill = "#ffcc00" if slot.index == 0 else "#f2f2f2"
        out.append(
            f'<polygon class="grid" points="{v.pts(corners)}" fill="{fill}" stroke="#000000" stroke-width="0.5"/>'
        )
    for j in model.jumps:
        x, y, h = j.position[0], j.position[1], j.heading
        size = max(model.width_m * 0.6, 6.0)
        tip = (x + math.cos(h) * size, y + math.sin(h) * size)
        a, b = _across(x, y, h, size * 0.6)
        opacity = "1" if j.in_base else "0.5"
        out.append(
            f'<polygon class="jump" points="{v.pts([tip, a, b])}" fill="#ffe14d" fill-opacity="{opacity}" '
            f'stroke="#000000" stroke-width="1"/>'
        )
        lx, ly = v.pt(x, y).split(",")
        out.append(f'<text x="{_f(float(lx) + 8)}" y="{_f(float(ly) - 8)}" fill="#000000">{escape(j.id)}</text>')
    colours = {"bridge": "#2f6fff", "flat": "#ff9f1a", "unannotated": "#ff2a2a"}
    for c in model.crossings:
        cx, cy = (float(t) for t in v.pt(*c.position).split(","))
        dash = "" if c.detected else ' stroke-dasharray="2 2"'
        out.append(
            f'<circle class="crossing" cx="{_f(cx)}" cy="{_f(cy)}" r="7" fill="{colours.get(c.type, "#ff2a2a")}" '
            f'stroke="#ffffff" stroke-width="2"{dash}/>'
        )
        out.append(f'<text x="{_f(cx + 9)}" y="{_f(cy + 4)}" fill="#ffffff">{escape(c.id)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def elevation_svg(model: TrackModel) -> str:
    width, height, pad = 1000, 320, 40.0
    longest = max(r.length_m for r in model.routes)
    zs = [s.z for r in model.routes for s in r.samples]
    z_lo, z_hi = min(zs), max(zs)
    if z_hi - z_lo < 10.0:
        mid = (z_hi + z_lo) / 2
        z_lo, z_hi = mid - 5.0, mid + 5.0
    sx = (width - 2 * pad) / longest
    sy = (height - 2 * pad) / (z_hi - z_lo)

    def pt(s: float, z: float) -> str:
        return f"{_f(pad + s * sx)},{_f(height - pad - (z - z_lo) * sy)}"

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="10" y="20">{escape(model.name)}: elevation (m) against distance (m); '
        f"z {z_lo:.1f} to {z_hi:.1f}</text>",
        f'<polyline points="{pt(0, z_lo)} {pt(longest, z_lo)}" stroke="#888888" stroke-width="1"/>',
        f'<polyline points="{pt(0, z_lo)} {pt(0, z_hi)}" stroke="#888888" stroke-width="1"/>',
        f'<text x="{_f(pad)}" y="{height - 12}">0</text>',
        f'<text x="{_f(width - pad - 40)}" y="{height - 12}">{longest:.0f} m</text>',
    ]
    for i, r in enumerate(model.routes):
        colour = ROUTE_COLOURS[i % len(ROUTE_COLOURS)]
        points = [(s.s, s.z) for s in r.samples]
        if r.closed:
            points.append((r.length_m, r.samples[0].z))
        out.append(
            f'<polyline data-route="{escape(r.id)}" points="{" ".join(pt(s, z) for s, z in points)}" '
            f'fill="none" stroke="{colour}" stroke-width="1.5"/>'
        )
        out.append(f'<text x="{_f(width - 160)}" y="{20 + 14 * (i + 1)}" fill="{colour}">{escape(r.id)}</text>')
    for c in model.crossings:
        if c.over_branch is None or not c.detected:
            continue
        b = c.branches[c.over_branch]
        out.append(
            f'<circle cx="{pt(b.s, b.z).split(",")[0]}" cy="{pt(b.s, b.z).split(",")[1]}" r="4" fill="#2f6fff"/>'
        )
        out.append(
            f'<text x="{_f(pad + b.s * sx + 6)}" y="{_f(height - pad - (b.z - z_lo) * sy - 6)}">{escape(c.id)}</text>'
        )
    for j in model.jumps:
        x, y = pt(j.s, j.position[2]).split(",")
        out.append(
            f'<polygon points="{x},{_f(float(y) - 8)} {_f(float(x) - 5)},{y} {_f(float(x) + 5)},{y}" fill="#e0b000"/>'
        )
        out.append(f'<text x="{_f(float(x) + 6)}" y="{_f(float(y) - 8)}">{escape(j.id)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
