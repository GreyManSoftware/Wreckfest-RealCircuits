"""Our own sponsor boards (category ``sponsorBoards``): ad-fence panels, mini billboards and billboards on posts,
with textures rendered here with Pillow. Stock ad props reference stock textures we can't change, so these are
meshes in the base scene, not ``#xref`` subscenes.

Configuration: ``environments.json`` ``"sponsor"``: ``enabled``, ``billboards`` / ``minis`` (caps per circuit),
``fenceShare`` (share of the stock ad-fence blocks that become ours), ``credit`` and ``sponsors``, a list of
sponsors, each with ``id`` (lower-case letters and digits), ``text``, optional ``subtext``, ``tagline``, ``logo``
(a PNG under this package), ``glyph`` ("instagram"), ``uppercase``, ``kinds`` (which boards carry it),
``share`` (weight among the sponsors of a kind), ``billboards`` (at most this many big boards), ``guarantee``
(one of its boards in view of every layout's start), ``lapGuarantee`` (one of its billboards in clear view
somewhere on every layout's lap), ``colourways`` (named) and ``designs`` (style, font, colourway). The JSON file
named in ``TRACKGEN_SPONSOR`` is merged over it (``{"enabled": false}`` turns the boards off; ``"sponsors":
{"<id>": {...}}`` changes one sponsor, e.g. its tagline). The planner (``dressing``) places them; ``build_scene``
turns those placements into one mesh (``sponsor_boards``) and writes the textures as TGA under
``data/track/<slug>/textures`` (bimage'd by the pipeline like the generated maps).

Asset frame (as the stock ad fences): the printed face is local **-Y** (it faces the road / the oncoming view),
the board runs along local X, the origin is on the ground at its middle.

Assets: ``sponsor_<id>_fence<k>`` (ad-fence panel), ``sponsor_<id>_billboard<k>`` (7 x 3.5 m on posts),
``sponsor_<id>_minibillboard<k>`` (3.5 x 1.75 m on posts, the billboard's texture at half size), for design k of
sponsor id, and ``sponsor_credit`` (the small "track by ..." plaque, ``"credit"`` in the configuration).
"""
from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache

import numpy as np

from . import envs
from .geometry import Mesh

KIND = "sponsor"
CATEGORY = "sponsorBoards"
FENCE = {"w": 3.84, "h": 1.0, "z0": 0.08, "t": 0.05}                # panel width, height, gap below, thickness
BILLBOARD = {"w": 7.0, "h": 3.5, "z0": 2.0, "t": 0.12, "post": 0.22, "sink": 0.6, "depth": 0.8}
MINI = {"w": 3.5, "h": 1.75, "z0": 1.0, "t": 0.1, "post": 0.16, "sink": 0.5, "depth": 0.6}  # the small banner board
CREDIT = {"w": 1.8, "h": 0.45, "z0": 0.55, "t": 0.06, "post": 0.08, "sink": 0.4, "depth": 0.4}  # the credit plaque
TEX = {"fence": (1024, 512), "board": (1024, 1024), "credit": (512, 256)}  # front art in the top half
POSTED = {"billboard": BILLBOARD, "minibillboard": MINI, "credit": CREDIT}       # panels on two posts
KINDS = ("fence", "billboard", "minibillboard")
CREDIT_DEFAULT = {"enabled": True, "text": "GreyManSoftware", "prefix": "track by", "style": "plaque",
                  "colourway": {"background": [52, 56, 60], "text": [176, 180, 178], "accent": [96, 100, 104],
                                "back": [60, 62, 66]}}
SPONSOR_DEFAULT = {"kinds": ["fence", "minibillboard"], "share": 1.0, "billboards": 0, "guarantee": False,
                   "lapGuarantee": False, "style": "slant", "font": "sans", "uppercase": False, "glyph": None,
                   "subtext": None, "tagline": None, "logo": None}
ASSET_RE = re.compile(r"sponsor_([a-z0-9]+)_(fence|billboard|minibillboard)(\d+)")
HERE = os.path.dirname(os.path.abspath(__file__))


def _merge(cfg: dict, over: dict) -> dict:
    """``over`` merged over ``cfg``: ``credit`` key by key, ``sponsors`` as a dict merges per id (unknown ids are
    added), anything else replaces."""
    out = dict(cfg)
    for k, v in over.items():
        if k == "sponsors" and isinstance(v, dict):
            have = [s for s in cfg.get("sponsors") or [] if isinstance(s, dict)]
            ids = {s.get("id") for s in have}
            out["sponsors"] = [{**s, **(v.get(s.get("id")) or {})} for s in have] + \
                              [{"id": i, **x} for i, x in v.items() if i not in ids and isinstance(x, dict)]
        elif k == "credit" and isinstance(v, dict) and isinstance(cfg.get("credit"), dict):
            out["credit"] = {**cfg["credit"], **v}
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def config() -> dict:
    cfg = dict(envs.table().get("sponsor") or {"enabled": False})
    path = os.environ.get("TRACKGEN_SPONSOR")
    if path:
        with open(path, encoding="utf-8") as f:
            cfg = _merge(cfg, json.load(f))
    return cfg


@lru_cache(maxsize=1)
def sponsors() -> tuple[dict, ...]:
    """The enabled sponsors, normalised: SPONSOR_DEFAULT under each, ``kinds`` limited to KINDS and ``designs``
    resolved to {style, font, colourway (dict), name}. A configuration without ``sponsors`` but with ``text``
    (the first format) is one sponsor ``handle`` on every kind with the start guarantee."""
    c = config()
    if not c.get("enabled"):
        return ()
    raw = c.get("sponsors")
    if raw is None and c.get("text"):
        raw = [{"id": "handle", "text": c["text"], "tagline": c.get("tagline"), "glyph": "instagram",
                "colourways": c.get("colourways") or [], "kinds": list(KINDS), "guarantee": True,
                "billboards": int(c.get("billboards", 6))}]
    out = []
    for s in raw or []:
        if not isinstance(s, dict) or s.get("enabled", True) is False or not s.get("text"):
            continue
        sid = str(s.get("id", ""))
        if not re.fullmatch(r"[a-z0-9]+", sid):
            raise ValueError(f"sponsor id {sid!r}: lower-case letters and digits only")
        cws = s.get("colourways") or {}
        if isinstance(cws, list):
            cws = {cw.get("name", str(n)): cw for n, cw in enumerate(cws)}
        designs = []
        for n, d in enumerate(s.get("designs") or [{"colourway": name} for name in cws]):
            cw = d.get("colourway")
            name = cw if isinstance(cw, str) else str(n)
            cw = cws.get(cw) if isinstance(cw, str) else cw
            if not isinstance(cw, dict):
                continue
            design = {"style": d.get("style", s.get("style", "slant")), "font": d.get("font", s.get("font", "sans")),
                      "colourway": cw, "name": f"{d.get('style', s.get('style', 'slant'))}-{name}",
                      "layout": d.get("layout", s.get("layout"))}
            fence = d.get("fence", s.get("fence"))  # another style / colourway / layout on fence panels
            if isinstance(fence, dict):
                fc = fence.get("colourway", cw)
                design["fence"] = {**design, **fence, "colourway": cws.get(fc) if isinstance(fc, str) else fc}
                design["fence"].pop("fence", None)
            designs.append(design)
        kinds = [k for k in s.get("kinds", SPONSOR_DEFAULT["kinds"]) if k in KINDS]
        if designs and kinds:
            out.append({**SPONSOR_DEFAULT, **s, "id": sid, "kinds": kinds, "designs": designs})
    return tuple(out)


def by_id(sid: str) -> dict | None:
    return next((s for s in sponsors() if s["id"] == sid), None)


def enabled() -> bool:
    return bool(sponsors())


def with_kind(kind: str) -> list[dict]:
    return [s for s in sponsors() if kind in s["kinds"]]


def share(s: dict, kind: str) -> float:
    """A sponsor's weight among the sponsors of ``kind`` (``share``: one number, or per kind)."""
    v = s.get("share", 1.0)
    return float((v.get(kind, 1.0) if isinstance(v, dict) else v) or 0.0)


def lap_sponsors() -> list[dict]:
    """The sponsors with ``lapGuarantee``: one of their billboards in clear view somewhere on every lap."""
    return [s for s in with_kind("billboard") if s.get("lapGuarantee")]


def credit() -> dict | None:
    """The credit settings (``"credit"``: ``text``, ``prefix``, ``style`` "plaque" | "line", ``colourway``,
    ``enabled``) over the defaults, or None when it is off (``"credit": false`` / ``{"enabled": false}``). It
    is independent of the handle boards being on."""
    c = config().get("credit", {})
    if c is False or c is None:
        return None
    out = {**CREDIT_DEFAULT, **(c if isinstance(c, dict) else {})}
    if not out.get("enabled", True) or not out.get("text") or out.get("style") not in ("plaque", "line", "subtle"):
        return None
    if out["style"] == "subtle":  # the generic name: the plaque
        out["style"] = "plaque"
    return out


def credit_line() -> str | None:
    c = credit()
    return " ".join(x for x in (c.get("prefix"), c["text"]) if x) if c else None


def asset(sid: str, kind: str, k: int) -> str:
    """Board ``kind`` of sponsor ``sid`` in design k (modulo its number of designs)."""
    return f"sponsor_{sid}_{kind}{k % len(by_id(sid)['designs'])}"


def asset_names() -> list[str]:
    out = [f"sponsor_{s['id']}_{kind}{k}" for s in sponsors() for kind in s["kinds"] for k in range(len(s["designs"]))]
    if credit():
        out.append("sponsor_credit")
    return out


def parse(name: str) -> tuple[str, str, int] | None:
    """(sponsor id, 'fence' | 'billboard' | 'minibillboard' | 'credit', design) for one of our assets, else None."""
    if name == "sponsor_credit":
        return "", "credit", 0
    m = ASSET_RE.fullmatch(name)
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def kind_of(name: str) -> tuple[str, int] | None:
    """(kind, design) for one of our assets, else None."""
    p = parse(name)
    return (p[1], p[2]) if p else None


def sponsor_of(name: str) -> str | None:
    p = parse(name)
    return p[0] if p and p[1] != "credit" else None


def is_handle(name: str) -> bool:
    """A sponsor board (every kind but the credit plaque)."""
    return sponsor_of(name) is not None


def tex_kind(kind: str) -> str:
    return "fence" if kind == "fence" else "board"


def material_of(name: str) -> str:
    sid, kind, k = parse(name)
    return "sponsor_credit" if kind == "credit" else f"sponsor_{sid}_{tex_kind(kind)}{k}"


def dims(kind: str) -> dict:
    return FENCE if kind == "fence" else POSTED[kind]


def asset_info(name: str) -> dict | None:
    k = kind_of(name)
    if k is None:
        return None
    if k[0] == "fence":
        return {"r": FENCE["w"] / 2.0, "h": FENCE["z0"] + FENCE["h"], "face": "-y", "width": FENCE["w"],
                "depth": 0.3, "mesh": True}
    B = POSTED[k[0]]
    return {"r": B["w"] / 2.0 + 0.3, "h": B["z0"] + B["h"], "face": "-y", "width": B["w"],
            "depth": B["depth"], "mesh": True}


def is_mesh(name: str) -> bool:
    return kind_of(name) is not None


class Picker:
    """Which sponsor and design the next board of a kind carries: of the sponsors with that kind (big billboards:
    under their ``billboards`` cap), the one furthest below its ``share``, ties to the first listed; designs
    rotate per sponsor and kind (minis start one design on from the fences, so neighbours differ). ``peek``
    names the asset, ``commit`` counts it once placed."""

    OFFSET = {"fence": 0, "minibillboard": 1, "billboard": 0}

    def __init__(self) -> None:
        self.count: dict[tuple[str, str], int] = {}

    def placed(self, sid: str, kind: str) -> int:
        return self.count.get((sid, kind), 0)

    def peek(self, kind: str, sid: str | None = None) -> str | None:
        best = None
        for s in with_kind(kind):
            if sid is not None and s["id"] != sid:
                continue
            n = self.placed(s["id"], kind)
            if kind == "billboard" and sid is None and n >= int(s.get("billboards") or 0):
                continue
            key = (n + 1) / max(share(s, kind), 1e-6)
            if best is None or key < best[0] - 1e-9:
                best = (key, s, n)
        if best is None:
            return None
        _key, s, n = best
        return asset(s["id"], kind, n + self.OFFSET[kind])

    def commit(self, name: str) -> None:
        sid, kind, _k = parse(name)
        self.count[(sid, kind)] = self.placed(sid, kind) + 1


# ----------------------------------------------------------------------------------------------- textures
def texture_rel(slug: str, name: str) -> str:
    """Data path (without extension) of an asset's texture (a mini billboard shares its design's billboard
    texture)."""
    return f"data/track/{slug}/textures/{slug}_{material_of(name)}_c"


_D = "/usr/share/fonts/truetype/dejavu/"
_W = "C:/Windows/Fonts/"
FONT_FILES = {  # the worker image has DejaVu only; the Windows names are for local previews
    "sans": (_D + "DejaVuSans-Bold.ttf", _W + "arialbd.ttf", _W + "segoeuib.ttf"),
    "regular": (_D + "DejaVuSans.ttf", _W + "arial.ttf", _W + "segoeui.ttf"),
    "serif": (_D + "DejaVuSerif-Bold.ttf", _W + "georgiab.ttf", _W + "timesbd.ttf"),
    "mono": (_D + "DejaVuSansMono-Bold.ttf", _W + "consolab.ttf", _W + "courbd.ttf"),
    "monoitalic": (_D + "DejaVuSansMono-BoldOblique.ttf", _W + "consolaz.ttf", _W + "courbi.ttf"),
}
# face -> (font family, stroke as a share of the size: a heavier weight, shear: a slanted italic)
FACES = {"sans": ("sans", 0.0, 0.0), "heavy": ("sans", 0.022, 0.0), "italic": ("sans", 0.0, 0.22),
         "heavyitalic": ("sans", 0.018, 0.22), "serif": ("serif", 0.0, 0.0), "mono": ("mono", 0.0, 0.0),
         "monoitalic": ("monoitalic", 0.0, 0.0), "regular": ("regular", 0.0, 0.0),
         "regularitalic": ("regular", 0.0, 0.2)}


@lru_cache(maxsize=64)
def _family(fam: str, size: int):
    from PIL import ImageFont

    for p in FONT_FILES[fam] + FONT_FILES["sans"]:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def _font(size: int, regular: bool = False):
    return _family("regular" if regular else "sans", size)


def _rgb(c) -> tuple[int, int, int]:
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    return tuple(int(v) for v in c)  # type: ignore[return-value]


def _mix(a, b, t: float) -> tuple[int, int, int]:
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(_rgb(a), _rgb(b)))  # type: ignore[return-value]


@lru_cache(maxsize=512)
def _ink(s: str, face: str, px: int, spacing: float = 0.0):
    """The text as an "L" mask cropped to its ink, in ``face`` (FACES) at ``px``, with ``spacing`` (share of the
    size) between letters."""
    from PIL import Image, ImageDraw

    fam, stroke, shear = FACES[face]
    f = _family(fam, px)
    sw = int(round(px * stroke))
    adv = [f.getlength(ch) for ch in s]
    H = int(px * 2 + 2 * sw)
    W = int(sum(adv) + spacing * px * len(s) + px * 2 + 2 * sw + shear * H)
    m = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(m)
    x, y = px * 0.5 + sw, px * 0.4 + sw
    if spacing:
        for ch, a in zip(s, adv):
            d.text((x, y), ch, font=f, fill=255, stroke_width=sw, stroke_fill=255)
            x += a + spacing * px
    else:
        d.text((x, y), s, font=f, fill=255, stroke_width=sw, stroke_fill=255)
    if shear:  # the top leans right
        m = m.transform(m.size, Image.AFFINE, (1, shear, -shear * H, 0, 1, 0), resample=Image.BICUBIC)
    bb = m.getbbox()
    return m.crop(bb) if bb else m


def _fit(s: str, face: str, max_w: float, max_h: float, spacing: float = 0.0):
    """The largest ink mask of ``s`` within max_w x max_h."""
    px = max(8, int(max_h * 1.6))
    while True:
        m = _ink(s, face, px, spacing)
        if (m.width <= max_w and m.height <= max_h) or px <= 8:
            return m
        px = max(8, min(px - 1, int(px * min(0.98, max_w / m.width, max_h / m.height))))


def _paste(img, m, x: float, y: float, colour) -> None:
    img.paste(_rgb(colour), (int(round(x)), int(round(y)), int(round(x)) + m.width, int(round(y)) + m.height), m)


def _block(img, box, main, sub=None, align: str = "center", sub_ratio: float = 0.5, gap: float = 0.28,
           third=None, third_ratio: float = 0.4):
    """One to three lines fitted into box (x0, y0, x1, y1) as a block, vertically centred: ``main``, ``sub`` and
    ``third`` are (text, face, colour[, spacing]); the sub line at most ``sub_ratio`` of the main line's height,
    the third ``third_ratio``."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    share_h = 1.0 if not sub else (0.62 if not third else 0.5)
    mm = _fit(main[0], main[1], w, h * share_h, *(main[3:4] or ()))
    lines = [(mm, main[2])]
    for ln, ratio in ((sub, sub_ratio), (third, third_ratio)):
        if ln:
            lines.append((_fit(ln[0], ln[1], w, mm.height * ratio, *(ln[3:4] or ())), ln[2]))
    g = mm.height * gap if sub else 0.0
    y = y0 + (h - sum(m.height for m, _c in lines) - g * (len(lines) - 1)) / 2.0
    for m, col in lines:
        x = x0 + (w - m.width) / 2.0 if align == "center" else x0
        _paste(img, m, x, y, col)
        y += m.height + g


def _glyph(draw, box, colour, width: int) -> None:
    """A camera glyph in the Instagram manner (rounded square, lens ring, flash dot), drawn from primitives."""
    x0, y0, x1, y1 = box
    s = x1 - x0
    draw.rounded_rectangle(box, radius=int(s * 0.28), outline=colour, width=width)
    c = ((x0 + x1) / 2, (y0 + y1) / 2)
    r = s * 0.23
    draw.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), outline=colour, width=width)
    d = s * 0.075
    fx, fy = x1 - s * 0.22, y0 + s * 0.22
    draw.ellipse((fx - d, fy - d, fx + d, fy + d), fill=colour)


def _lines(sp: dict, fence: bool):
    """(name, sub line, tagline) as printed: ``uppercase`` applies to the name; no tagline on fence panels."""
    tag = None if fence or not sp.get("tagline") else sp["tagline"]
    return (sp["text"].upper() if sp.get("uppercase") else sp["text"]), sp.get("subtext"), tag


# Styles: each draws the printed face of one design on img (W x H) -------------------------------------------
def _style_slant(img, sp, cw, face, fence):
    """A slanted accent band at the left end, thin rules top and bottom, the glyph and the text centred."""
    from PIL import ImageDraw

    W, H = img.size
    bg, fg, acc = _rgb(cw["background"]), _rgb(cw["text"]), _rgb(cw["accent"])
    d = ImageDraw.Draw(img)
    band = int(W * 0.06)
    d.polygon([(0, 0), (band * 2, 0), (band, H), (0, H)], fill=acc)
    d.rectangle((0, H - max(4, H // 22), W, H), fill=acc)
    d.rectangle((0, 0, W, max(3, H // 40)), fill=acc)
    left = band * 2 + W * 0.03
    pad = H * 0.16
    if sp.get("glyph"):
        g = (H - 2 * pad) * (0.78 if fence else 0.4)
        _glyph(d, (left, (H - g) / 2, left + g, (H + g) / 2), fg, max(3, int(g * 0.095)))
        left += g + W * 0.035
    text, sub, tag = _lines(sp, fence)
    _block(img, (left, pad, W - W * 0.04, H - pad), (text, face, fg), (tag, "sans", acc) if tag else None)
    del bg


def _style_stripes(img, sp, cw, face, fence):
    """Racing stripes (accent, text colour, accent) slanting across the right end, the glyph and the text in a
    slanted italic from the left, a rule along the bottom."""
    from PIL import ImageDraw

    W, H = img.size
    fg, acc = _rgb(cw["text"]), _rgb(cw["accent"])
    d = ImageDraw.Draw(img)
    sw, gap, lean = W * 0.028, W * 0.014, H * (0.45 if fence else 0.22)
    x = W - W * 0.03 - 3 * sw - 2 * gap
    stripes_x = x
    for col in (acc, fg, acc):
        d.polygon([(x + lean * 0.5, 0), (x + sw + lean * 0.5, 0), (x + sw - lean * 0.5, H), (x - lean * 0.5, H)],
                  fill=col)
        x += sw + gap
    d.rectangle((0, H - max(4, H // 18), stripes_x - lean * 0.5 - gap, H), fill=acc)
    pad = H * 0.15
    left = W * 0.04
    if sp.get("glyph"):
        g = (H - 2 * pad) * (0.74 if fence else 0.38)
        _glyph(d, (left, (H - g) / 2, left + g, (H + g) / 2), fg, max(3, int(g * 0.095)))
        left += g + W * 0.03
    text, sub, tag = _lines(sp, fence)
    _block(img, (left, pad, stripes_x - lean - gap, H - pad), (text, face, fg),
           (tag, "italic", acc) if tag else None, align="left")


def _style_badge(img, sp, cw, face, fence):
    """Chequered strips top and bottom, the glyph in a round accent badge at the left, the text centred."""
    from PIL import ImageDraw

    W, H = img.size
    bg, fg, acc = _rgb(cw["background"]), _rgb(cw["text"]), _rgb(cw["accent"])
    d = ImageDraw.Draw(img)
    sq = max(6, int(H * (0.075 if fence else 0.05)))
    for row, y in enumerate((0, H - 2 * sq)):
        for j in range(2):
            for i in range(0, W // sq + 1):
                if (i + j + row) % 2 == 0:
                    d.rectangle((i * sq, y + j * sq, i * sq + sq - 1, y + j * sq + sq - 1), fill=fg)
    inner0, inner1 = 2 * sq, H - 2 * sq
    left = W * 0.035
    if sp.get("glyph"):
        g = (inner1 - inner0) * 0.86 if fence else H * 0.4
        cy = (inner0 + inner1) / 2
        d.ellipse((left, cy - g / 2, left + g, cy + g / 2), fill=acc)
        gg = g * 0.56
        on = _rgb(cw.get("onAccent", bg))
        _glyph(d, (left + (g - gg) / 2, cy - gg / 2, left + (g + gg) / 2, cy + gg / 2), on, max(3, int(gg * 0.1)))
        left += g + W * 0.03
    text, sub, tag = _lines(sp, fence)
    pad = (inner1 - inner0) * 0.12
    _block(img, (left, inner0 + pad, W - W * 0.04, inner1 - pad), (text, face, fg),
           (tag, "sans", acc) if tag else None)


def _style_split(img, sp, cw, face, fence):
    """A frame in the accent colour, a slanted accent block at the left carrying the glyph, the text on the
    rest."""
    from PIL import ImageDraw

    W, H = img.size
    fg, acc = _rgb(cw["text"]), _rgb(cw["accent"])
    on = _rgb(cw.get("onAccent", cw["text"]))
    d = ImageDraw.Draw(img)
    fr = max(4, int(H * 0.045))
    d.rectangle((0, 0, W - 1, H - 1), outline=acc, width=fr)
    left = W * 0.05
    if sp.get("glyph"):
        bw = H * (1.05 if fence else 0.52)
        d.polygon([(0, 0), (bw + H * 0.18, 0), (bw - H * 0.18, H), (0, H)], fill=acc)
        g = H * (0.56 if fence else 0.3)
        gx = (bw - H * 0.12 - g) / 2
        _glyph(d, (gx, (H - g) / 2, gx + g, (H + g) / 2), on, max(3, int(g * 0.095)))
        left = bw + H * 0.18 + W * 0.03
    text, sub, tag = _lines(sp, fence)
    pad = H * 0.17
    _block(img, (left, pad, W - W * 0.045, H - pad), (text, face, fg), (tag, "sans", acc) if tag else None)


@lru_cache(maxsize=8)
def _logo(rel: str):
    from PIL import Image

    return Image.open(os.path.join(HERE, *rel.split("/"))).convert("RGBA")


def _style_wordmark(img, sp, cw, face, fence):
    """A brand board for legibility at distance (the mockups Chris approved): optional ``top`` / ``bottom`` bands,
    a ``bar`` down the left edge and faint diagonal ``stripe``s, the logo at the left, the name very large in a
    heavy face, the sub line (e.g. the rest of the domain) in ``sub`` and the tagline in ``tagline`` under it;
    left-aligned beside a logo, else centred."""
    from PIL import Image, ImageDraw

    W, H = img.size
    fg = _rgb(cw["text"])
    d = ImageDraw.Draw(img)
    if cw.get("stripe"):
        st = _rgb(cw["stripe"])
        step = max(8, int(H * 0.37))
        for x in range(-H, W + H, step):
            d.polygon([(x, H), (x + step * 0.6, H), (x + step * 0.6 + H * 0.57, 0), (x + H * 0.57, 0)], fill=st)
    y0, y1, left = H * 0.08, H * 0.92, W * 0.04
    if cw.get("top"):
        b = max(4, int(H * 0.06))
        d.rectangle((0, 0, W, b), fill=_rgb(cw["top"]))
        y0 = b + H * 0.06
    if cw.get("bottom"):
        b = max(4, int(H * 0.06))
        d.rectangle((0, H - b, W, H), fill=_rgb(cw["bottom"]))
        y1 = H - b - H * 0.06
    if cw.get("bar"):
        b = max(4, int(W * 0.018))
        d.rectangle((0, 0, b, H), fill=_rgb(cw["bar"]))
        left = b + W * 0.04
    text, sub, tag = _lines(sp, fence)
    align = "center"
    if sp.get("logo"):
        lg = _logo(sp["logo"])
        lh = (y1 - y0) * 0.86
        lw = lh * lg.width / lg.height
        if lw > W * 0.3:
            lw, lh = W * 0.3, W * 0.3 * lg.height / lg.width
        im = lg.resize((max(1, int(lw)), max(1, int(lh))), Image.LANCZOS)
        img.paste(im, (int(left), int((y0 + y1 - lh) / 2)), im if im.mode == "RGBA" else None)
        left += lw + W * 0.04
        align = "left"
    _block(img, (left, y0, W - W * 0.04, y1), (text, face, fg),
           (sub, "sans", _rgb(cw.get("sub", fg))) if sub else None, align=align, sub_ratio=0.62, gap=0.3,
           third=(tag, "sans", _rgb(cw.get("tagline", fg))) if tag else None, third_ratio=0.5)


def _style_logoband(img, sp, cw, face, fence):
    """A supplied full logo (on the board's own background) centred and as large as fits above a band in the
    primary colour that carries the glyph and the text (e.g. the Instagram handle) in ``onPrimary``, a thin
    accent rule between them."""
    from PIL import Image, ImageDraw

    W, H = img.size
    pri, acc = _rgb(cw["primary"]), _rgb(cw.get("accent", cw["primary"]))
    on = _rgb(cw.get("onPrimary", (255, 255, 255)))
    d = ImageDraw.Draw(img)
    band = int(H * (0.32 if fence else 0.27))
    rule = max(2, int(H * 0.025))
    d.rectangle((0, H - band, W, H), fill=pri)
    d.rectangle((0, H - band - rule, W, H - band), fill=acc)
    if sp.get("logo"):
        lg = _logo(sp["logo"])
        aw, ah = W * 0.94, (H - band - rule) * 0.9
        s = min(aw / lg.width, ah / lg.height)
        im = lg.resize((max(1, int(lg.width * s)), max(1, int(lg.height * s))), Image.LANCZOS)
        img.paste(im, (int((W - im.width) / 2), int((H - band - rule - im.height) / 2)),
                  im if im.mode == "RGBA" else None)
    text, _sub, _tag = _lines(sp, True)
    pad = band * 0.17
    m = _fit(text, face, W * 0.8, band - 2 * pad)
    g = m.height * 1.05 if sp.get("glyph") else 0.0
    x = (W - m.width - (g + m.height * 0.35 if g else 0.0)) / 2
    yc = H - band / 2
    if g:
        _glyph(d, (x, yc - g / 2, x + g, yc + g / 2), on, max(3, int(g * 0.1)))
        x += g + m.height * 0.35
    _paste(img, m, x, yc - m.height / 2, on)


def _style_banner(img, sp, cw, face, fence):
    """A recreated banner logo for one too small to scale up cleanly: the text in ``parts`` (text, colourway key)
    in a heavy slanted face with a white then a dark outline, slanted accent/white dashes under it and the
    logo's own sub line (``subtext``) in a slanted regular face below, centred on a dark background with broad
    diagonal ``stripe``s (the mockup)."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    W, H = img.size
    d = ImageDraw.Draw(img)
    dark, acc = _rgb(cw.get("dark", (20, 20, 20))), _rgb(cw.get("accent", (220, 20, 20)))
    if cw.get("stripe"):
        st = _rgb(cw["stripe"])
        step = max(8, int(H * 0.37))
        for x in range(-H, W + H, step):
            d.polygon([(x, H), (x + step * 0.6, H), (x + step * 0.6 + H * 0.57, 0), (x + H * 0.57, 0)], fill=st)
    parts = sp.get("parts") or [[sp["text"], "text"]]
    full = "".join(p[0] for p in parts)
    sub = sp.get("subtext")
    top, bot = H * 0.08, H * 0.92
    stripe_h = (bot - top) * (0.1 if fence else 0.08)
    sub_h = (bot - top) * (0.0 if not sub else (0.2 if fence else 0.16))
    area_h = (bot - top - stripe_h - sub_h) * 0.86
    fam, stroke, shear = FACES["heavyitalic"]
    px = max(8, int(area_h * 1.4))
    while True:
        m = _ink(full, "heavyitalic", px)
        o = max(2, int(px * 0.07)) | 1
        if (m.width + 4 * o <= W * 0.92 and m.height + 4 * o <= area_h) or px <= 8:
            break
        px = max(8, min(px - 1, int(px * min(0.98, W * 0.92 / (m.width + 4 * o), area_h / (m.height + 4 * o)))))
    # uncropped masks of each prefix share an origin, so a part is its prefix minus the one before
    raw = []
    for n in range(len(parts) + 1):
        s = "".join(p[0] for p in parts[:n])
        f = _family(fam, px)
        sw = int(round(px * stroke))
        Hc = int(px * 2 + 2 * sw)
        Wc = int(f.getlength(full) + px * 2 + 2 * sw + shear * Hc) + 8 * o
        mk = Image.new("L", (Wc, Hc + 8 * o), 0)
        if s:
            ImageDraw.Draw(mk).text((px * 0.5 + sw + 4 * o, px * 0.4 + sw + 4 * o), s, font=f, fill=255,
                                    stroke_width=sw, stroke_fill=255)
        if shear:
            mk = mk.transform(mk.size, Image.AFFINE, (1, shear, -shear * mk.height, 0, 1, 0), resample=Image.BICUBIC)
        raw.append(mk)
    whole = raw[-1]
    white = whole.filter(ImageFilter.MaxFilter(o))
    outer = white.filter(ImageFilter.MaxFilter(o))
    bb = outer.getbbox()
    ox = (W - (bb[2] - bb[0])) / 2 - bb[0]
    sm = _fit(sub, "regularitalic", W * 0.8, sub_h * 0.7) if sub else None
    group = (bb[3] - bb[1]) + stripe_h * 0.65 + (sub_h if sub else 0.0)
    oy = top + (bot - top - group) / 2 - bb[1]
    # under the text: a run of slanted dashes, accent and white, as the logo's swoosh
    sy0 = oy + bb[3] - stripe_h * 0.35
    sy1 = sy0 + stripe_h
    seg, lean = W * 0.055, stripe_h * 0.9
    for k, x in enumerate(np.arange(ox + bb[0], ox + bb[2] - seg, seg * 1.1)):
        d.polygon([(x + lean, sy0), (x + seg + lean, sy0), (x + seg, sy1), (x, sy1)],
                  fill=acc if k % 2 == 0 else _rgb(cw.get("dash", (255, 255, 255))))
    if sm is not None:
        _paste(img, sm, (W - sm.width) / 2, sy1 + (sub_h - sm.height) / 2, cw.get("subColour", (255, 255, 255)))
    sh = max(1, o // 2)
    img.paste(dark, (int(ox) + sh, int(oy) + sh, int(ox) + sh + outer.width, int(oy) + sh + outer.height), outer)
    img.paste(_rgb(cw.get("outline", (255, 255, 255))), (int(ox), int(oy), int(ox) + white.width,
                                                          int(oy) + white.height), white)
    for n, (_s, key) in enumerate(parts):
        part = ImageChops.subtract(raw[n + 1], raw[n])
        img.paste(_rgb(cw.get(key, cw["text"])), (int(ox), int(oy), int(ox) + part.width, int(oy) + part.height), part)


def _prefix_masks(strings: list[str], face: str, px: int):
    """[whole, part 1, part 2, ...]: "L" masks of the joined strings and of each part in place (each part is its
    prefix minus the one before, so the parts keep the line's spacing), cropped to the whole line's ink."""
    from PIL import Image, ImageChops, ImageDraw

    fam, stroke, shear = FACES[face]
    f = _family(fam, px)
    sw = int(round(px * stroke))
    full = "".join(strings)
    Hc = int(px * 2 + 2 * sw)
    Wc = int(f.getlength(full) + px * 2 + 2 * sw + shear * Hc)
    pre = []
    for n in range(len(strings) + 1):
        m = Image.new("L", (Wc, Hc), 0)
        if n:
            ImageDraw.Draw(m).text((px * 0.5 + sw, px * 0.4 + sw), "".join(strings[:n]), font=f, fill=255,
                                   stroke_width=sw, stroke_fill=255)
        if shear:
            m = m.transform(m.size, Image.AFFINE, (1, shear, -shear * Hc, 0, 1, 0), resample=Image.BICUBIC)
        pre.append(m)
    bb = pre[-1].getbbox() or (0, 0, Wc, Hc)
    return [pre[-1].crop(bb)] + [ImageChops.subtract(pre[n + 1], pre[n]).crop(bb) for n in range(len(strings))]


def _style_livery(img, sp, cw, layout: dict):
    """A racing-livery board described by ``layout`` (Chris's v2 mockups): positions and sizes are shares of the
    face (x of the width, y and sizes of the height), colours are colourway keys or literal colours. Layers in
    order: ``gradient`` [top, bottom] (or ``carbon`` [weave, base, top, bottom]), ``stripes`` {x, colours, width,
    gap, slope}, ``chequer`` {box, cell, colours, fade}, ``polys`` [{points, colour}] (bands, plates), ``lines``
    [{from, to, colour, width}], ``logo`` {box [x, y, w, h], sticker (white border width), key (key out a white
    background) or crop (trim it), align}, ``pills`` [{at, height, maxW, parts [[text, colour]], fill}] (an
    "address bar" with the parts in their colours) and ``texts`` [{text ("{text}", "{subtext}", "{tagline}" or literal), upper, at [x, y] (top
    left of the ink), size, maxW, face, fill, stroke, strokeFill, shadow}]."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    W, H = img.size

    def col(c):
        return _rgb(cw[c]) if isinstance(c, str) and c in cw else _rgb(c)

    def pt(p):
        return (p[0] * W, p[1] * H)

    def vgrad(g0, g1):
        return Image.composite(Image.new("RGB", (W, H), col(g1)), Image.new("RGB", (W, H), col(g0)),
                               Image.linear_gradient("L").resize((W, H)))

    if layout.get("carbon"):
        weave, base, g0, g1 = layout["carbon"]
        cell = max(4, int(H * 0.017))
        img.paste(col(base), (0, 0, W, H))
        d = ImageDraw.Draw(img)
        for y in range(0, H, cell * 2):
            for x in range(0, W, cell * 2):
                yy = y if (x // (cell * 2) + y // (cell * 2)) % 2 == 0 else y + cell
                d.rectangle((x, yy, x + 2 * cell - 1, yy + cell - 1), fill=col(weave))
        img.paste(Image.blend(img, vgrad(g0, g1), 0.5))
    elif layout.get("gradient"):
        img.paste(vgrad(*layout["gradient"]))
    d = ImageDraw.Draw(img)
    st = layout.get("stripes")
    if st:
        x, w, gap, slope = st["x"] * W, st["width"] * W, st.get("gap", 0.02) * W, st.get("slope", 0.55) * H
        for c in st["colours"]:
            d.polygon([(x, H), (x + w, H), (x + w + slope, 0), (x + slope, 0)], fill=col(c))
            x += w + gap
    ch = layout.get("chequer")
    if ch:
        x0, y0, x1, y1 = ch["box"][0] * W, ch["box"][1] * H, ch["box"][2] * W, ch["box"][3] * H
        cell = max(4, int(ch.get("cell", 0.043) * H))
        c1, c2 = (col(c) for c in ch.get("colours", ((255, 255, 255), (0, 0, 0))))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        cols = max(1, int((x1 - x0) // cell))
        for j in range(max(1, int((y1 - y0) // cell))):
            for i in range(cols):
                a = int(255 * (1 - i / cols)) if ch.get("fade", True) else 255
                dl.rectangle((x0 + i * cell, y0 + j * cell, x0 + (i + 1) * cell - 1, y0 + (j + 1) * cell - 1),
                             fill=(c1 if (i + j) % 2 == 0 else c2) + (a,))
        img.paste(layer, (0, 0), layer)
    for poly in layout.get("polys", []):
        d.polygon([pt(p) for p in poly["points"]], fill=col(poly["colour"]))
    for ln in layout.get("lines", []):
        d.line([pt(ln["from"]), pt(ln["to"])], fill=col(ln["colour"]), width=max(1, int(ln.get("width", 0.009) * H)))
    lg = layout.get("logo")
    if lg and sp.get("logo"):
        im = _logo(sp["logo"])
        if lg.get("key"):  # key out a white background: alpha 0 above 235 in every channel, a ramp from 200
            a = np.asarray(im.convert("RGB"), dtype=np.float64).min(axis=2)
            alpha = np.clip((235.0 - a) / 35.0, 0.0, 1.0) * np.asarray(im.getchannel("A"), dtype=np.float64)
            im = im.copy()
            im.putalpha(Image.fromarray(np.round(alpha).astype(np.uint8)))
            bb = im.getchannel("A").getbbox()
            im = im.crop(bb) if bb else im
        elif lg.get("crop"):  # trim a white background to the logo's content
            bb = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, (255, 255, 255))).convert(
                "L").point(lambda v: 255 if v > 24 else 0).getbbox()
            im = im.crop(bb) if bb else im
        bx, by, bw, bh = lg["box"][0] * W, lg["box"][1] * H, lg["box"][2] * W, lg["box"][3] * H
        s = min(bw / im.width, bh / im.height)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
        if lg.get("sticker"):  # the logo as a sticker: its outline grown by a white border
            b = max(1, int(lg["sticker"] * H))
            pad = Image.new("RGBA", (im.width + 2 * b, im.height + 2 * b), (0, 0, 0, 0))
            pad.paste(im, (b, b), im if im.mode == "RGBA" else None)
            base = Image.new("RGBA", pad.size, col(lg.get("stickerColour", (255, 255, 255))) + (0,))
            base.putalpha(pad.getchannel("A").filter(ImageFilter.MaxFilter(2 * b + 1)))
            base.paste(pad, (0, 0), pad)
            im = base
        x = bx if lg.get("align") == "left" else bx + (bw - im.width) / 2
        img.paste(im, (int(x), int(by + (bh - im.height) / 2)), im if im.mode == "RGBA" else None)
    fields = {"text": sp["text"], "subtext": sp.get("subtext") or "", "tagline": sp.get("tagline") or "",
              "url": sp.get("url") or ""}
    for pl in layout.get("pills", []):  # an "address bar": a rounded pill with the parts in their colours
        parts = [(t.format(**fields), c) for t, c in pl["parts"]]
        h = pl.get("height", 0.12) * H
        masks = _prefix_masks([t for t, _c in parts], pl.get("face", "sans"), max(8, int(h * 0.55)))
        k = min(1.0, (pl.get("maxW", 0.9) * W - h * 0.76) / masks[0].width)
        if k < 1.0:
            masks = _prefix_masks([t for t, _c in parts], pl.get("face", "sans"), max(8, int(h * 0.55 * k)))
        x0, y0 = pl["at"][0] * W, pl["at"][1] * H
        d.rounded_rectangle((x0, y0, x0 + masks[0].width + h * 0.76, y0 + h), radius=int(h / 2),
                            fill=col(pl.get("fill", (255, 255, 255))))
        for m, (_t, c) in zip(masks[1:], parts):
            _paste(img, m, x0 + h * 0.38, y0 + (h - masks[0].height) / 2, col(c))
    for t in layout.get("texts", []):
        s = t["text"].format(**fields)
        s = s.upper() if t.get("upper") else s
        if not s.strip():
            continue
        face = t.get("face", "heavyitalic")
        px = max(8, int(t.get("size", 0.1) * H * 1.35))  # size: the ink height of capitals, roughly
        max_w = t.get("maxW", 0.9) * W
        while True:
            m = _ink(s, face, px)
            if m.width <= max_w or px <= 8:
                break
            px = max(8, min(px - 1, int(px * max_w / m.width)))
        o = int(t.get("stroke", 0.0) * H)
        pad = Image.new("L", (m.width + 2 * o, m.height + 2 * o), 0)
        pad.paste(m, (o, o))
        outline = pad.filter(ImageFilter.MaxFilter(2 * o + 1)) if o else pad
        x, y = t["at"][0] * W - o, t["at"][1] * H - o
        if t.get("shadow") is not None:
            k = max(1, int(H * 0.012))
            _paste(img, outline.filter(ImageFilter.GaussianBlur(max(1, H * 0.006))), x + k, y + k, col(t["shadow"]))
        if o:
            _paste(img, outline, x, y, col(t.get("strokeFill", (0, 0, 0))))
        _paste(img, pad, x, y, col(t.get("fill", "text")))


STYLES = {"slant": _style_slant, "stripes": _style_stripes, "badge": _style_badge, "split": _style_split,
          "wordmark": _style_wordmark, "logoband": _style_logoband, "banner": _style_banner}


def render_front(sp: dict, design: dict, tex: str, size: tuple[int, int]):
    """The printed face (PIL image, top-down rows) of one sponsor design on a fence panel (``tex`` "fence") or a
    billboard / mini billboard ("board")."""
    from PIL import Image

    if tex == "fence" and design.get("fence"):
        design = design["fence"]
    cw = design["colourway"]
    img = Image.new("RGB", size, _rgb(cw["background"]))
    face = design.get("font", "sans")
    if face not in FACES:
        face = "sans"
    if design.get("style") == "livery":
        _style_livery(img, sp, cw, design.get("layout") or {})
    else:
        STYLES.get(design.get("style"), _style_slant)(img, sp, cw, face, tex == "fence")
    return img


def render_credit(cw: dict, line: str, size: tuple[int, int]):
    """The credit plaque's face: muted slate, a thin border, the credit line in a regular weight, centred."""
    from PIL import Image, ImageDraw

    W, H = size
    img = Image.new("RGB", (W, H), _rgb(cw["background"]))
    d = ImageDraw.Draw(img)
    b = max(2, H // 28)
    d.rectangle((b * 2, b * 2, W - 1 - b * 2, H - 1 - b * 2), outline=_rgb(cw["accent"]), width=b)
    m = _fit(line, "regular", W * 0.86, H * 0.42)
    _paste(img, m, (W - m.width) / 2, (H - m.height) / 2, cw["text"])
    return img


def credit_strip(img, cw: dict, line: str) -> None:
    """The "line" credit style: the credit in small muted type at the bottom right of a billboard face."""
    W, H = img.size
    m = _fit(line, "regular", W * 0.4, H * 0.04)
    col = _mix(cw["background"], cw["text"], 0.45)
    _paste(img, m, W * 0.96 - m.width, H - H * 0.075 - m.height, col)


def write_textures(root: str, slug: str, credit_style: str | None = None, assets=None) -> dict[str, str]:
    """Render the board textures to <root>/data/track/<slug>/textures/*.tga: those of ``assets`` (the placed
    boards; None: every design and kind) and the credit plaque when it is on. ``credit_style`` is the style the
    planner settled on (``info.sponsorCredit``: "line" when the plaque found no room). Returns {material key: data
    path}."""
    from PIL import Image

    from .maps import write_tga

    out: dict[str, str] = {}
    cr = credit()
    style = (credit_style or cr["style"]) if cr else None
    line = credit_line() if style == "line" else None
    names = asset_names() if assets is None else sorted(set(assets))
    seen: set[str] = set()
    for name in names:
        if not is_handle(name) or material_of(name) in seen:
            continue
        key = material_of(name)
        seen.add(key)
        sid, kind, k = parse(name)
        sp = by_id(sid)
        design = sp["designs"][k % len(sp["designs"])]
        tex = tex_kind(kind)
        W, H = TEX[tex]
        sheet = Image.new("RGB", (W, H), _rgb(design["colourway"].get("back", (70, 72, 76))))
        front = render_front(sp, design, tex, (W, H // 2))
        if line and tex == "board":
            credit_strip(front, design["colourway"], line)
        sheet.paste(front, (0, 0))
        rel = texture_rel(slug, name)
        write_tga(os.path.join(root, *rel.split("/")) + ".tga", np.asarray(sheet, dtype=np.float64)[::-1])
        out[key] = rel
    if style == "plaque":
        cw = cr["colourway"]
        W, H = TEX["credit"]
        sheet = Image.new("RGB", (W, H), _rgb(cw.get("back", (60, 62, 66))))
        sheet.paste(render_credit(cw, credit_line(), (W, H // 2)), (0, 0))
        rel = texture_rel(slug, "sponsor_credit")
        write_tga(os.path.join(root, *rel.split("/")) + ".tga", np.asarray(sheet, dtype=np.float64)[::-1])
        out["sponsor_credit"] = rel
    return out


def material_specs(textures: dict[str, str]) -> dict[str, dict]:
    """Material specs (materials.build format) for the boards: albedo in slot 1, the stock zero-spec map in 9, a
    metal surface (they are solid but far behind the run-off)."""
    return {key: {"name": f"{key}#metal",
                  "slots": [(1, rel + ".tga", 1, (1.0, 1.0)),
                            (9, "data/art/textures/default_zero_spec_s.tga", 1, (1.0, 1.0))]}
            for key, rel in textures.items()}



# ----------------------------------------------------------------------------------------------- meshes
def _box(m: Mesh, centre, ax, ay, w: float, d: float, z0: float, z1: float, mat: str, front_uv, back_uv) -> None:
    """A box w along ax, d along ay, from z0 to z1; the -ay face gets ``front_uv`` (u0, v0, u1, v1), the rest
    ``back_uv``."""
    c = np.asarray(centre, dtype=np.float64)
    hx, hy = np.append(ax * w / 2.0, 0.0), np.append(ay * d / 2.0, 0.0)
    base = np.array([c[0], c[1], 0.0])
    P = {}
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz, z in ((0, z0), (1, z1)):
                P[(sx, sy, sz)] = m.vert(base + sx * hx + sy * hy + np.array([0.0, 0.0, z]))
    u0, v0, u1, v1 = front_uv
    b0, c0, b1, c1 = back_uv
    out = lambda v: np.append(v, 0.0)  # noqa: E731
    # front (-Y): left to right as seen from the front is +X... seen from -Y looking at +Y, +X is to the right
    m.oriented_face([P[(-1, -1, 0)], P[(1, -1, 0)], P[(1, -1, 1)], P[(-1, -1, 1)]], mat, out(-ay),
                    [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
    m.oriented_face([P[(1, 1, 0)], P[(-1, 1, 0)], P[(-1, 1, 1)], P[(1, 1, 1)]], mat, out(ay),
                    [(b0, c0), (b1, c0), (b1, c1), (b0, c1)])
    for sx in (-1, 1):
        m.oriented_face([P[(sx, -1, 0)], P[(sx, 1, 0)], P[(sx, 1, 1)], P[(sx, -1, 1)]], mat, out(ax * sx),
                        [(b0, c0), (b1, c0), (b1, c1), (b0, c1)])
    m.oriented_face([P[(-1, -1, 1)], P[(1, -1, 1)], P[(1, 1, 1)], P[(-1, 1, 1)]], mat, np.array([0, 0, 1.0]),
                    [(b0, c0), (b1, c0), (b1, c1), (b0, c1)])
    m.oriented_face([P[(-1, -1, 0)], P[(-1, 1, 0)], P[(1, 1, 0)], P[(1, -1, 0)]], mat, np.array([0, 0, -1.0]),
                    [(b0, c0), (b1, c0), (b1, c1), (b0, c1)])


FRONT_UV = (0.0, 0.5, 1.0, 1.0)     # TGA rows run bottom-up: the top half of the sheet is v 0.5..1
BACK_UV = (0.02, 0.02, 0.98, 0.45)


def build_mesh(placements) -> Mesh | None:
    """All sponsor placements as one solid mesh (``col = true``), in TrackModel coordinates."""
    m = Mesh("sponsor_boards", kind=KIND)
    for p in placements:
        k = kind_of(p.asset)
        if k is None:
            continue
        kind, cw = k
        mat = material_of(p.asset)
        ax = np.array([np.cos(p.yaw), np.sin(p.yaw)])          # local +X
        ay = np.array([-np.sin(p.yaw), np.cos(p.yaw)])         # local +Y (the printed face is -Y)
        z = float(p.z)
        if kind == "fence":
            F = FENCE
            _box(m, (p.x, p.y), ax, ay, F["w"], F["t"], z + F["z0"], z + F["z0"] + F["h"], mat, FRONT_UV, BACK_UV)
            for sx in (-1, 1):  # two short feet
                q = np.array([p.x, p.y]) + ax * sx * (F["w"] / 2.0 - 0.15) + ay * 0.08
                _box(m, q, ax, ay, 0.06, 0.25, z - 0.2, z + F["z0"] + 0.2, mat, BACK_UV, BACK_UV)
        else:  # billboard, mini billboard, credit plaque: a panel on two posts
            B = POSTED[kind]
            _box(m, (p.x, p.y), ax, ay, B["w"], B["t"], z + B["z0"], z + B["z0"] + B["h"], mat, FRONT_UV, BACK_UV)
            inset = min(1.0, B["w"] * 0.15)
            for sx in (-1, 1):
                q = np.array([p.x, p.y]) + ax * sx * (B["w"] / 2.0 - inset) + ay * (B["t"] / 2.0 + B["post"] / 2.0)
                _box(m, q, ax, ay, B["post"], B["post"], z - B["sink"], z + B["z0"] + B["h"] - 0.2, mat,
                     BACK_UV, BACK_UV)
    return None if m.empty else m


# ----------------------------------------------------------------------------------------------- visibility
EYE_M = 1.2                      # driver's eye above the road
VIEW = {"billboard": (12.0, 220.0, 0.3), "minibillboard": (10.0, 150.0, 0.3), "fence": (8.0, 90.0, 0.25)}
VIEW_CONE_DEG = 75.0             # the board within this of the driving direction
NO_BLOCK = {"gantry", "flags", "staff", "spectators", "cornerMarkers", "brakeBoards", "barriers", "crowdFence",
            "adFence", "cameras"}  # low or thin: they don't hide a board


class Blockers:
    """The larger props as vertical cylinders (long ones, e.g. grandstands and billboards, as a row of them along
    local X), for sight lines. ``items``: (asset, category, x, y, z, yaw, scale); props under 0.5 m radius or
    1.5 m tall and the NO_BLOCK categories are left out. ``owner`` maps each cylinder to its item."""

    def __init__(self, items):
        xy, r, top, owner = [], [], [], []
        for n, (asset, cat, x, y, z, yaw, scale) in enumerate(items):
            if cat in NO_BLOCK:
                continue
            info = envs.asset_info(asset)
            h = float(info.get("h", 2.0)) * scale
            if h < 1.5:
                continue
            if "width" in info and info.get("face") != "x":
                w, d = float(info["width"]) * scale, float(info.get("depth", 1.0)) * scale
                k = int(min(12, max(2, math.ceil(w / max(d, 0.5)) + 1)))
                ax = np.array([math.cos(yaw), math.sin(yaw)])
                rad = max(d / 2.0, w / (2.0 * (k - 1)))
                for t in np.linspace(-w / 2.0, w / 2.0, k):
                    xy.append((x + ax[0] * t, y + ax[1] * t))
                    r.append(rad)
                    top.append(z + h)
                    owner.append(n)
                continue
            rr = float(info["r"]) * scale * 0.6
            if rr < 0.5:
                continue
            xy.append((x, y))
            r.append(rr)
            top.append(z + h)
            owner.append(n)
        self.xy = np.array(xy, dtype=np.float64).reshape(-1, 2)
        self.r = np.array(r, dtype=np.float64)
        self.top = np.array(top, dtype=np.float64)
        self.owner = np.array(owner, dtype=np.int64)

    def hits(self, eyes: np.ndarray, target: np.ndarray, skip: int = -1) -> np.ndarray:
        """(N eyes, M cylinders) bool: the cylinder stands on the sight line eye -> target where the line is below
        its top (the target's own item ``skip`` left out)."""
        if not len(self.xy):
            return np.zeros((len(eyes), 0), dtype=bool)
        a = eyes[:, None, :2]
        ab = target[None, None, :2] - a
        L2 = np.maximum((ab ** 2).sum(-1), 1e-9)
        t = np.clip(((self.xy[None] - a) * ab).sum(-1) / L2, 0.0, 1.0)
        foot = a + t[..., None] * ab
        d = np.hypot(self.xy[None, :, 0] - foot[..., 0], self.xy[None, :, 1] - foot[..., 1])
        z = eyes[:, None, 2] + t * (target[2] - eyes[:, None, 2])
        out = (d < self.r[None]) & (self.top[None] > z) & (t > 0.02) & (t < 0.98)
        if skip >= 0:
            out &= (self.owner != skip)[None]
        return out


def targets(asset: str, x: float, y: float, z: float, yaw: float) -> tuple[np.ndarray, np.ndarray]:
    """Three points on a board's printed face (left, middle, right at mid height, just in front of it) and the
    face's outward direction (local -Y)."""
    B = dims(kind_of(asset)[0])
    ax = np.array([math.cos(yaw), math.sin(yaw)])
    front = np.array([math.sin(yaw), -math.cos(yaw)])
    zc = z + B["z0"] + B["h"] / 2.0
    pts = [np.append(np.array([x, y]) + ax * u * B["w"] + front * (B["t"] / 2.0 + 0.05), zc)
           for u in (-0.35, 0.0, 0.35)]
    return np.array(pts), front


def visible(asset: str, x: float, y: float, z: float, yaw: float, eyes: np.ndarray, dirs: np.ndarray, terrain,
            blockers: Blockers | None, skip: int = -1) -> tuple[bool, int]:
    """Is the board in clear view from one of ``eyes`` (N x 3, driver's eye points) looking along ``dirs`` (N x 2,
    driving direction)? In range for its kind, within VIEW_CONE_DEG of the driving direction, its printed face
    turned towards the eye, and at least two of its three face points (``targets``) on sight lines clear of the
    terrain (``terrain(xy) -> z``) and of the larger props. Returns (visible, index of the first eye in order that
    sees it, else -1)."""
    kind = kind_of(asset)[0]
    if kind == "credit" or not len(eyes):
        return False, -1
    lo, hi, face_min = VIEW[kind]
    pts, front = targets(asset, x, y, z, yaw)
    v = pts[1][None, :2] - eyes[:, :2]
    dist = np.hypot(v[:, 0], v[:, 1])
    u = v / np.maximum(dist, 1e-9)[:, None]
    cone = (u * dirs).sum(1) >= math.cos(math.radians(VIEW_CONE_DEG))
    facing = (-u @ front) >= face_min
    cand = np.nonzero((dist >= lo) & (dist <= hi) & cone & facing)[0]
    if not len(cand):
        return False, -1
    E = eyes[cand]
    clear = np.zeros((len(cand), 3), dtype=bool)
    t = np.linspace(0.03, 0.97, 32)
    for k, T in enumerate(pts):
        line = E[:, None, :] + t[None, :, None] * (T - E)[:, None, :]
        ground = np.asarray(terrain(line[..., :2].reshape(-1, 2)), dtype=np.float64).reshape(len(E), len(t))
        ok = ~(ground + 0.15 > line[..., 2]).any(1)
        if blockers is not None:
            ok &= ~blockers.hits(E, T, skip).any(1)
        clear[:, k] = ok
    good = np.nonzero(clear.sum(1) >= 2)[0]
    return (True, int(cand[good[0]])) if len(good) else (False, -1)
