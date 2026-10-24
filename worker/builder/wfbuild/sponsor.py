"""Our own sponsor boards (category ``sponsorBoards``): ad-fence panels and billboards on posts, with textures
rendered here (the handle text in bold, an Instagram-style camera glyph we draw ourselves, a few colourways).
Stock ad props reference stock textures we can't change, so these are meshes in the base scene, not
``#xref`` subscenes.

Configuration: ``environments.json`` ``"sponsor"`` (text, colourways, how many boards, on/off), overridden by the
JSON file named in ``TRACKGEN_SPONSOR`` (merged over it; ``{"enabled": false}`` turns the boards off). The planner
(``dressing``) places them; ``build_scene`` turns those placements into one mesh (``sponsor_boards``) and writes
the textures as TGA under ``data/track/<slug>/textures`` (bimage'd by the pipeline like the generated maps).

Asset frame (as the stock ad fences): the printed face is local **-Y** (it faces the road / the oncoming view),
the board runs along local X, the origin is on the ground at its middle.

Kinds: ``sponsor_fence<k>`` (ad-fence panel), ``sponsor_billboard<k>`` (7 x 3.5 m on posts),
``sponsor_minibillboard<k>`` (the half-size infield board of the last fallback, same texture as the billboard) and
``sponsor_credit`` (the small "track by ..." plaque, ``"credit"`` in the configuration). Every layout gets at least
one handle board in view of its grid or start/finish straight (``dressing.Planner.sponsor_guarantee``, checked
with ``visible`` here against the terrain and the larger props).
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache

import numpy as np

from . import envs
from .geometry import Mesh

KIND = "sponsor"
CATEGORY = "sponsorBoards"
FENCE = {"w": 3.84, "h": 1.0, "z0": 0.08, "t": 0.05}                # panel width, height, gap below, thickness
BILLBOARD = {"w": 7.0, "h": 3.5, "z0": 2.0, "t": 0.12, "post": 0.22, "sink": 0.6, "depth": 0.8}
MINI = {"w": 3.5, "h": 1.75, "z0": 1.0, "t": 0.1, "post": 0.16, "sink": 0.5, "depth": 0.6}  # the infield fallback board
CREDIT = {"w": 1.8, "h": 0.45, "z0": 0.55, "t": 0.06, "post": 0.08, "sink": 0.4, "depth": 0.4}  # the credit plaque
TEX = {"fence": (1024, 512), "billboard": (1024, 1024), "credit": (512, 256)}  # front art in the top half
POSTED = {"billboard": BILLBOARD, "minibillboard": MINI, "credit": CREDIT}       # panels on two posts
CREDIT_DEFAULT = {"enabled": True, "text": "GreyManSoftware", "prefix": "track by", "style": "plaque",
                  "colourway": {"background": [52, 56, 60], "text": [176, 180, 178], "accent": [96, 100, 104],
                                "back": [60, 62, 66]}}


@lru_cache(maxsize=1)
def config() -> dict:
    cfg = dict(envs.table().get("sponsor") or {"enabled": False})
    path = os.environ.get("TRACKGEN_SPONSOR")
    if path:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def enabled() -> bool:
    c = config()
    return bool(c.get("enabled")) and bool(c.get("text")) and bool(c.get("colourways"))


def n_colourways() -> int:
    return len(config().get("colourways") or [])


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


def asset_names() -> list[str]:
    out = []
    if enabled():
        out = [f"sponsor_{kind}{k}" for kind in ("fence", "billboard", "minibillboard") for k in range(n_colourways())]
    if credit():
        out.append("sponsor_credit")
    return out


def kind_of(asset: str) -> tuple[str, int] | None:
    """('fence' | 'billboard' | 'minibillboard' | 'credit', colourway) for one of our assets, else None."""
    if asset == "sponsor_credit":
        return "credit", 0
    for kind in ("minibillboard", "billboard", "fence"):
        pre = f"sponsor_{kind}"
        if asset.startswith(pre) and asset[len(pre):].isdigit():
            return kind, int(asset[len(pre):])
    return None


def is_handle(asset: str) -> bool:
    """A board carrying the handle (every kind but the credit plaque)."""
    k = kind_of(asset)
    return k is not None and k[0] != "credit"


def material_of(asset: str) -> str:
    kind, k = kind_of(asset)
    return "sponsor_credit" if kind == "credit" else f"sponsor_{'billboard' if kind == 'minibillboard' else kind}{k}"


def dims(kind: str) -> dict:
    return FENCE if kind == "fence" else POSTED[kind]


def asset_info(asset: str) -> dict | None:
    k = kind_of(asset)
    if k is None:
        return None
    if k[0] == "fence":
        return {"r": FENCE["w"] / 2.0, "h": FENCE["z0"] + FENCE["h"], "face": "-y", "width": FENCE["w"],
                "depth": 0.3, "mesh": True}
    B = POSTED[k[0]]
    return {"r": B["w"] / 2.0 + 0.3, "h": B["z0"] + B["h"], "face": "-y", "width": B["w"],
            "depth": B["depth"], "mesh": True}


def is_mesh(asset: str) -> bool:
    return kind_of(asset) is not None


# ----------------------------------------------------------------------------------------------- textures
def texture_rel(slug: str, kind: str, k: int) -> str:
    """Data path (without extension) of a board's texture (a mini billboard shares the billboard's)."""
    if kind == "credit":
        return f"data/track/{slug}/textures/{slug}_sponsor_credit_c"
    if kind == "minibillboard":
        kind = "billboard"
    return f"data/track/{slug}/textures/{slug}_sponsor_{kind}{k}_c"


FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
              "C:/Windows/Fonts/segoeuib.ttf")
FONT_PATHS_REGULAR = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
                      "C:/Windows/Fonts/segoeui.ttf") + FONT_PATHS


def _font(size: int, regular: bool = False):
    from PIL import ImageFont

    for p in (FONT_PATHS_REGULAR if regular else FONT_PATHS):
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def _rgb(c) -> tuple[int, int, int]:
    return tuple(int(v) for v in c)  # type: ignore[return-value]


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


def render_front(kind: str, cw: dict, text: str, size: tuple[int, int]):
    """The printed face (PIL image, top-down rows): background, a slanted accent stripe, the glyph and the text in
    bold, sized to fill the board."""
    from PIL import Image, ImageDraw

    W, H = size
    bg, fg, acc = _rgb(cw["background"]), _rgb(cw["text"]), _rgb(cw["accent"])
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    # accent: a slanted band at the left end and a thin rule along the bottom, as circuit sponsor boards have
    band = int(W * 0.06)
    d.polygon([(0, 0), (band * 2, 0), (band, H), (0, H)], fill=acc)
    d.rectangle((0, H - max(4, H // 22), W, H), fill=acc)
    d.rectangle((0, 0, W, max(3, H // 40)), fill=acc)
    left = band * 2 + W * 0.03
    pad = H * 0.16
    g = (H - 2 * pad) * (0.78 if kind == "fence" else 0.62)
    gx0 = left
    gy0 = (H - g) / 2
    _glyph(d, (gx0, gy0, gx0 + g, gy0 + g), fg, max(3, int(g * 0.095)))
    tx0 = gx0 + g + W * 0.035
    avail = W - tx0 - W * 0.04
    def fit(s: str, start: int, max_h: float):
        size_px = start
        while True:
            f = _font(size_px)
            bb = d.textbbox((0, 0), s, font=f)
            if (bb[2] - bb[0] <= avail and bb[3] - bb[1] <= max_h) or size_px <= 8:
                return f, bb
            size_px -= 2

    tag = cw.get("tagline") if kind == "billboard" else None
    f, bb = fit(text, int(H * 0.62), H - 2 * pad if not tag else (H - 2 * pad) * 0.55)
    th = bb[3] - bb[1]
    lines = [(text, f, bb, fg)]
    gap = 0.0
    if tag:
        f2, bb2 = fit(tag, int(th * 0.55), th * 0.5)
        lines.append((tag, f2, bb2, acc))
        gap = th * 0.35
    total = sum(b[3] - b[1] for _s, _f, b, _c in lines) + gap
    y = (H - total) / 2
    for s, ff, b, col in lines:  # centred as a block beside the glyph
        d.text((tx0 + (avail - (b[2] - b[0])) / 2 - b[0], y - b[1]), s, font=ff, fill=col)
        y += b[3] - b[1] + gap
    return img


def _mix(a, b, t: float) -> tuple[int, int, int]:
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(_rgb(a), _rgb(b)))  # type: ignore[return-value]


def _fit(d, s: str, start: int, max_w: float, max_h: float, regular: bool = False):
    size_px = start
    while True:
        f = _font(size_px, regular)
        bb = d.textbbox((0, 0), s, font=f)
        if (bb[2] - bb[0] <= max_w and bb[3] - bb[1] <= max_h) or size_px <= 8:
            return f, bb
        size_px -= 2


def render_credit(cw: dict, line: str, size: tuple[int, int]):
    """The credit plaque's face: muted slate, a thin border, the credit line in a regular weight, centred."""
    from PIL import Image, ImageDraw

    W, H = size
    img = Image.new("RGB", (W, H), _rgb(cw["background"]))
    d = ImageDraw.Draw(img)
    b = max(2, H // 28)
    d.rectangle((b * 2, b * 2, W - 1 - b * 2, H - 1 - b * 2), outline=_rgb(cw["accent"]), width=b)
    f, bb = _fit(d, line, int(H * 0.5), W * 0.86, H * 0.52, regular=True)
    d.text(((W - (bb[2] - bb[0])) / 2 - bb[0], (H - (bb[3] - bb[1])) / 2 - bb[1]), line, font=f,
           fill=_rgb(cw["text"]))
    return img


def credit_strip(img, cw: dict, line: str) -> None:
    """The "line" credit style: the credit in small muted type at the bottom right of a billboard face, just
    above the accent rule."""
    from PIL import ImageDraw

    W, H = img.size
    d = ImageDraw.Draw(img)
    rule = max(4, H // 22)
    f, bb = _fit(d, line, int(H * 0.06), W * 0.4, H * 0.055, regular=True)
    col = _mix(cw["background"], cw["text"], 0.45)
    d.text((W * 0.96 - (bb[2] - bb[0]) - bb[0], H - rule - H * 0.02 - (bb[3] - bb[1]) - bb[1]), line, font=f, fill=col)


def write_textures(root: str, slug: str, credit_style: str | None = None) -> dict[str, str]:
    """Render the board textures to <root>/data/track/<slug>/textures/*.tga (every handle kind and colourway, and
    the credit plaque when it is on); ``credit_style`` is the style the planner settled on (``info.sponsorCredit``:
    "line" when the plaque found no room); returns {material key: data path}."""
    from PIL import Image

    from .maps import write_tga

    out: dict[str, str] = {}
    cfg = config()
    cr = credit()
    style = (credit_style or cr["style"]) if cr else None
    line = credit_line() if style == "line" else None

    def save(kind: str, k: int, sheet) -> None:
        rel = texture_rel(slug, kind, k)
        write_tga(os.path.join(root, *rel.split("/")) + ".tga", np.asarray(sheet, dtype=np.float64)[::-1])
        out["sponsor_credit" if kind == "credit" else f"sponsor_{kind}{k}"] = rel

    if enabled():
        for k, cw in enumerate(cfg["colourways"]):
            for kind in ("fence", "billboard"):
                W, H = TEX[kind]
                sheet = Image.new("RGB", (W, H), _rgb(cw.get("back", (70, 72, 76))))
                front = render_front(kind, {**cw, "tagline": cfg.get("tagline") if kind == "billboard" else None},
                                     cfg["text"], (W, H // 2))
                if line and kind == "billboard":
                    credit_strip(front, cw, line)
                sheet.paste(front, (0, 0))
                save(kind, k, sheet)
    if style == "plaque":
        cw = cr["colourway"]
        W, H = TEX["credit"]
        sheet = Image.new("RGB", (W, H), _rgb(cw.get("back", (60, 62, 66))))
        sheet.paste(render_credit(cw, credit_line(), (W, H // 2)), (0, 0))
        save("credit", 0, sheet)
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
