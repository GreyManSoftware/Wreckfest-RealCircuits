"""Merge per-circuit track mod zips into one Workshop bundle mod (used by tools/Build-TrackBundle.ps1).

    py tools/build_track_bundle.py --config tools/bundles/msvanglesey.json [--zip-dir E:\\WreckfestMods]
        [--zip a.zip --zip b.zip ...] [--slug S --title T] [--out-dir DIR] [--game-dir DIR] [--keep-folder]

Each member zip is one generated mod (`<slug>/data/...`, `modinfo.modi`, `preview.jpg`, `build-report.json`).
The bundle keeps every member's `data/` tree as it is and gets its own root files: `modinfo.modi` (the format
BagEdit's Mod Publish writes), `preview.jpg` (a collage of the members' maps), `CREDITS.txt` and `bundle.json`
(the members, for Install-TrackMod.ps1 -Bundle). It fails, and writes no zip, on:

- an unsafe or unexpected zip entry, a path two members share (case-insensitive), a file that would replace a
  stock game file, or a file type a generated mod doesn't ship;
- a track, environment, event, layout or weather list id used by two members, or one translation key with two
  texts;
- an event/weather reference that resolves neither in the bundle nor in the game;
- any per-track check: scne_read.limits on every base scene (the stock scene limits), and on every layout
  check_export.check_driving_order and check_export.check_track_data (the shipped .tcat), the same functions
  the builder tests run;
- an IP address, Steam ID, e-mail address or password in the bundle's text;
- the Workshop limits: title 128 bytes, description and change note 8,000 bytes, preview under 1 MB.

Stdlib + numpy (the checks) + Pillow (the preview). Output: <out-dir>/<slug>.zip (written atomically) and a
JSON summary on stdout; exit 0 when everything passed, 1 when a check failed, 2 on bad input.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "worker", "builder"), os.path.join(REPO, "worker", "builder", "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wf_modlist  # noqa: E402

GAME_DIR = r"D:\SteamLibrary\steamapps\common\Wreckfest"
SLUG_RE = re.compile(r"^[a-z0-9_]+$")
DATA_EXT = {".scne", ".tcat", ".bmap", ".evse", ".envi", ".weli"}
MEMBER_ROOT_FILES = {"modinfo.modi", "preview.jpg", "build-report.json"}
VISIBILITY = {"public": 0, "friends": 1, "private": 2, "hidden": 2}  # Steam ERemoteStoragePublishedFileVisibility
TAG_TRACK = 8  # modinfo tag bits: 1 car, 2 skin, 8 track, 16 object (Toolbox bagwrite)
LIMITS = {"titleBytes": 128, "descriptionBytes": 8000, "changeNoteBytes": 8000, "previewBytes": 1_000_000}
SECRET_PATTERNS = {
    "IPv4 address": re.compile(rb"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    "Steam ID": re.compile(rb"7656119\d{10}"),
    "e-mail address": re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "password": re.compile(rb"(?i)password\s*="),
}


class BundleError(Exception):
    """Bad input (exit 2)."""


class Checks:
    """PASS/FAIL lines go to ``out`` (stderr, or the --log file; failures also to stderr)."""

    def __init__(self, out=None) -> None:
        self.failures: list[str] = []
        self.passed = 0
        self.out = out or sys.stderr

    def __call__(self, ok: bool, what: str, detail: str = "") -> bool:
        line = f"{'PASS' if ok else 'FAIL'}: {what}{(' - ' + detail) if detail else ''}"
        print(line, file=self.out, flush=True)
        if not ok and self.out is not sys.stderr:
            print(line, file=sys.stderr)
        if ok:
            self.passed += 1
        else:
            self.failures.append(what + (f" ({detail})" if detail else ""))
        return ok


# --- bag text helpers ----------------------------------------------------------------------------------------

def _wftext(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<I", len(b)) + b


def modinfo_bytes(title: str, desc: str, change_note: str, visibility: int, published_id: int,
                  preview: str = "preview.jpg", tagbits: int = TAG_TRACK) -> bytes:
    """modinfo.modi as BagEdit's Mod Publish writes it (Workshop items: lz4 bag format 4, tag 'idom', version 0):
    title, preview file, description, latest change note (u32 byte length + UTF-8 each), u32 visibility
    (0 public, 1 friends, 2 private/hidden), u32 tag bits, u64 Published Id."""
    payload = (_wftext(title) + _wftext(preview) + _wftext(desc) + _wftext(change_note)
               + struct.pack("<IIQ", visibility, tagbits, published_id))
    return wf_modlist.bag_encode(wf_modlist.Bag(4, b"idom", 0, payload))


def modinfo_read(data: bytes) -> dict:
    p = wf_modlist.bag_decode(data).payload
    pos, out = 0, []
    for _ in range(4):
        (n,) = struct.unpack_from("<I", p, pos)
        out.append(p[pos + 4:pos + 4 + n].decode("utf-8"))
        pos += 4 + n
    vis, tags, pid = struct.unpack_from("<IIQ", p, pos)
    return {"title": out[0], "preview": out[1], "description": out[2], "changeNote": out[3], "visibility": vis,
            "tagBits": tags, "publishedId": pid, "trailingBytes": len(p) - pos - 16}


def translations(payload: bytes) -> list[tuple[str, str]]:
    """(key, text) of every 'nart' (tran) section: bagwrite writes header, version 2, count 1, key, text."""
    out, p = [], 0
    while (p := payload.find(b"nart", p)) >= 0:
        try:
            ver, num, kn = struct.unpack_from("<iiI", payload, p + 4)
            if ver == 2 and num == 1 and 0 < kn < 200:
                key = payload[p + 16:p + 16 + kn].decode("utf-8")
                (tn,) = struct.unpack_from("<I", payload, p + 16 + kn)
                out.append((key, payload[p + 20 + kn:p + 20 + kn + tn].decode("utf-8")))
        except (struct.error, UnicodeDecodeError):
            pass
        p += 4
    return out


# Event list order (Wreckfest_x64.exe, bgeventdb.cpp's sort): state class (Final 2 -> 0, Developer 1 -> 1,
# Mod 4 -> 2, others 9), environment id, position in data/property/event/order.evol (none in stock), event id
# (the .evse file name). The in-game picker lists an environment's events in that order.
STATE_CLASS = {2: 0, 1: 1, 4: 2}


def event_order(events: dict[str, bytes]) -> list[str]:
    """Event ids of one environment ({id: .evse payload}) in the order the game lists them."""
    return sorted(events, key=lambda e: (STATE_CLASS.get(struct.unpack_from("<i", events[e], 0)[0], 9), e))


def data_refs(payload: bytes) -> set[str]:
    return {m.decode() for m in re.findall(rb"data/[A-Za-z0-9_./-]+\.[a-z0-9]{3,4}", payload)}


# --- members -------------------------------------------------------------------------------------------------

def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_member(path: str, check: Checks) -> dict:
    """Validate a member zip; returns its slug, data entries (rel path -> ZipInfo), report and display name."""
    if not os.path.isfile(path):
        raise BundleError(f"zip not found: {path}")
    zf = zipfile.ZipFile(path)
    tops, data, bad = set(), {}, []
    root: dict[str, zipfile.ZipInfo] = {}
    for info in zf.infolist():
        name = info.filename
        parts = [x for x in name.split("/") if x]
        if not parts:
            continue
        if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in parts:
            bad.append(f"unsafe entry {name}")
            continue
        tops.add(parts[0])
        if info.is_dir():
            continue
        if len(parts) == 1:
            bad.append(f"top-level file {name}")
        elif len(parts) == 2:
            root[parts[1]] = info
        elif parts[1] != "data":
            bad.append(f"outside data/: {name}")
        else:
            rel = "/".join(parts[1:])
            ext = os.path.splitext(rel)[1].lower()
            if any(x.startswith(".") for x in parts) or ext not in DATA_EXT:
                bad.append(f"unexpected file type {rel}")
            data[rel] = info
    if len(tops) != 1:
        raise BundleError(f"{path}: expected one top-level folder, found {sorted(tops)}")
    slug = tops.pop()
    if not SLUG_RE.match(slug):
        raise BundleError(f"{path}: invalid mod folder '{slug}'")
    extra = sorted(set(root) - MEMBER_ROOT_FILES)
    check(not bad and not extra, f"{slug}: zip holds only a generated mod (data/ plus {', '.join(sorted(MEMBER_ROOT_FILES))})",
          "; ".join(bad[:5] + [f"unexpected root file {x}" for x in extra[:5]]))
    report = json.loads(zf.read(root["build-report.json"])) if "build-report.json" in root else {}
    name = slug
    if "modinfo.modi" in root:
        name = modinfo_read(zf.read(root["modinfo.modi"]))["title"] or slug
    preview = zf.read(root["preview.jpg"]) if "preview.jpg" in root else None
    return {"slug": slug, "zip": os.path.abspath(path), "zf": zf, "data": data, "report": report, "name": name,
            "preview": preview, "zipSha256": sha256(path), "builtAt": report.get("finishedAt")}


def ids_of(member: dict) -> dict[str, set[str]]:
    ids: dict[str, set[str]] = {k: set() for k in ("track", "environment", "event", "layout", "weather list")}
    for rel in member["data"]:
        parts = rel.split("/")
        stem, ext = os.path.splitext(parts[-1])
        if parts[:2] == ["data", "track"] and len(parts) >= 4:
            ids["track"].add(parts[2])
        if ext == ".envi":
            ids["environment"].add(stem)
        elif ext == ".evse":
            ids["event"].add(stem)
        elif ext == ".weli":
            ids["weather list"].add(stem)
        elif ext == ".scne" and parts[:2] == ["data", "track"] and not stem.endswith("_base"):
            ids["layout"].add(stem)
    return ids


# --- preview -------------------------------------------------------------------------------------------------

def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    windir = os.environ.get("WINDIR", r"C:\Windows")
    names = (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]) + ["DejaVuSans-Bold.ttf"]
    for n in names:
        for d in (os.path.join(windir, "Fonts"), "/usr/share/fonts/truetype/dejavu"):
            try:
                return ImageFont.truetype(os.path.join(d, n), size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def map_panel(jpg: bytes):
    """The track outline of a member's generated preview.jpg: since 2026-10-24 a bold white outline on the right of
    a dark card (worker/pipeline/images.py), cropped around its white stroke; older zips' green map panel."""
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(jpg)).convert("RGB")
    a = np.asarray(im).astype(int)
    white = a.min(axis=2) > 200
    # right of the card's text column (images.card: the outline's box starts 58 % + pad (h / 20) from the right)
    left = int(a.shape[1] * 0.42 - a.shape[0] // 20)
    white[:, :left] = False
    ys, xs = np.nonzero(white)
    if len(xs) > 500 and not ((a[:, :, 1] > a[:, :, 0] + 12) & (a[:, :, 1] > 60)).mean() > 0.2:
        m = int(0.08 * max(xs.max() - xs.min(), ys.max() - ys.min()))
        return im.crop((max(left, int(xs.min()) - m), max(0, int(ys.min()) - m),
                        min(a.shape[1], int(xs.max()) + 1 + m), min(a.shape[0], int(ys.max()) + 1 + m)))
    green = (a[:, :, 1] > a[:, :, 0] + 12) & (a[:, :, 1] > 60)
    green[:, : a.shape[1] // 2] = False
    ys, xs = np.nonzero(green)
    if len(xs) < 1000:
        return im
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    # tighter: around the track outline (the non-green pixels inside the panel), with a margin
    sub = a[y0 + 2:y1 - 2, x0 + 2:x1 - 2]
    ty, tx = np.nonzero(~((sub[:, :, 1] > sub[:, :, 0] + 12) & (sub[:, :, 1] > 60)))
    if len(tx) > 200:
        m = int(0.06 * max(tx.max() - tx.min(), ty.max() - ty.min()))
        bx, by = x0 + 2, y0 + 2
        x0, x1 = max(x0, bx + int(tx.min()) - m), min(x1, bx + int(tx.max()) + m)
        y0, y1 = max(y0, by + int(ty.min()) - m), min(y1, by + int(ty.max()) + m)
    return im.crop((x0, y0, x1, y1))


def make_preview(title: str, subtitle: str, tiles: list[tuple[str, bytes | None]], limit: int) -> bytes:
    """1280 x 720 (16:9) collage: title band, then the members' maps in a grid with their names."""
    from PIL import Image, ImageDraw
    W, H, top, pad = 1280, 720, 116, 14
    bg, tile_bg, fg, sub, accent = (23, 23, 26), (34, 35, 40), (240, 240, 240), (170, 170, 175), (200, 30, 30)
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 9, H), fill=accent)
    d.text((40, 18), title, font=_font(52), fill=fg)
    d.text((42, 80), subtitle, font=_font(22, bold=False), fill=sub)
    n = len(tiles)
    cols = 4 if n > 6 else max(1, min(n, 3))
    rows = -(-n // cols)
    tw = (W - 40 - pad * (cols + 1)) // cols
    th = (H - top - pad * (rows + 1)) // rows
    name_h = 40
    nf = _font(24)
    for k, (name, jpg) in enumerate(tiles):
        r, c = divmod(k, cols)
        x0, y0 = 30 + pad + c * (tw + pad), top + pad + r * (th + pad)
        d.rounded_rectangle((x0, y0, x0 + tw, y0 + th), radius=10, fill=tile_bg)
        if jpg:
            m = map_panel(jpg)
            box_w, box_h = tw - 16, th - name_h - 16
            s = min(box_w / m.width, box_h / m.height)
            m = m.resize((max(1, int(m.width * s)), max(1, int(m.height * s))), Image.LANCZOS)
            im.paste(m, (x0 + (tw - m.width) // 2, y0 + 8 + (box_h - m.height) // 2))
        tl = d.textlength(name, font=nf)
        f = nf if tl <= tw - 12 else _font(max(14, int(24 * (tw - 12) / tl)))
        tl = d.textlength(name, font=f)
        d.text((x0 + (tw - tl) / 2, y0 + th - name_h + 4), name, font=f, fill=fg)
    for q in (90, 85, 80, 75, 70, 60):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() < limit:
            return buf.getvalue()
    raise BundleError("preview.jpg doesn't fit under the Workshop limit")


# --- text ----------------------------------------------------------------------------------------------------

def km(m: float) -> str:
    return f"{m / 1000:.2f} km"


def description(cfg: dict, members: list[dict]) -> str:
    n_lay = sum(len(m["layouts"]) for m in members)
    lines = [cfg.get("intro", "").format(circuits=len(members), layouts=n_lay).strip(), "",
             "[h2]Circuits and layouts[/h2]", "[list]"]
    blurbs = cfg.get("blurbs", {})
    for m in members:
        lays = ", ".join(f"{l['name']} {km(l['lapM'])}" for l in m["layouts"])
        lines.append(f"[*][b]{m['name']}[/b]: {lays}")
        if blurbs.get(m["slug"]):
            lines.append(blurbs[m["slug"]])
    lines += ["[/list]", cfg.get("lapNote", "Lap lengths as built."), "",
              "[h2]Using it[/h2]", *cfg.get("usage", []), "",
              "[h2]Credits[/h2]", "[list]", *[f"[*]{c}" for c in cfg["credits"]], "[/list]",
              cfg["disclaimer"]]
    if cfg.get("homepage"):
        lines += ["", f"Made by GreyManSoftware: [url={cfg['homepage']}]{cfg['homepage']}[/url]"]
    return "\n".join(lines).strip() + "\n"


def credits_txt(cfg: dict, members: list[dict]) -> str:
    out = [cfg["title"], "=" * len(cfg["title"]), "", "Circuits and layouts (lap length as built):"]
    for m in members:
        out.append(f"- {m['name']}: " + ", ".join(f"{l['name']} {km(l['lapM'])}" for l in m["layouts"]))
    out += ["", "Data sources and licences:"] + [f"- {c}" for c in cfg.get("creditsLong", cfg["credits"])]
    out += ["", cfg["disclaimer"], ""]
    if cfg.get("homepage"):
        out += [f"Made by GreyManSoftware: {cfg['homepage']}", ""]
    return "\n".join(out)


# --- main ----------------------------------------------------------------------------------------------------

def build(args: argparse.Namespace) -> dict:
    cfg = {}
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["slug"] = args.slug or cfg.get("slug")
    cfg["title"] = args.title or cfg.get("title")
    if not cfg["slug"] or not SLUG_RE.match(cfg["slug"]):
        raise BundleError(f"invalid bundle slug {cfg['slug']!r} (expected [a-z0-9_]+)")
    if not cfg["title"]:
        raise BundleError("no bundle title")
    for k in ("credits", "disclaimer"):
        if not cfg.get(k):
            raise BundleError(f"config needs '{k}'")
    slug = cfg["slug"]
    zip_dir = args.zip_dir or cfg.get("zipDir") or r"E:\WreckfestMods"
    zips = args.zip or [os.path.join(zip_dir, f"{t}.zip") for t in cfg.get("tracks", [])]
    if not zips:
        raise BundleError("no member zips (pass --zip or list 'tracks' in the config)")
    out_dir = args.out_dir or zip_dir
    game = args.game_dir or GAME_DIR
    if not os.path.isdir(os.path.join(game, "data")):
        raise BundleError(f"game data not found under {game} (pass --game-dir)")
    vis_name = str(cfg.get("visibility", "private")).lower()
    if vis_name not in VISIBILITY:
        raise BundleError(f"visibility {vis_name!r}: expected one of {sorted(VISIBILITY)}")
    published_id = int(cfg.get("publishedId", 0))

    check = Checks(getattr(args, "log_file", None))
    members = [read_member(z, check) for z in zips]
    slugs = [m["slug"] for m in members]
    check(len(set(slugs)) == len(slugs) and slug not in slugs, "member mods are distinct and differ from the bundle",
          ", ".join(slugs))

    # Paths: one owner each (Windows is case-insensitive); nothing replaces a stock file
    owner: dict[str, str] = {}
    clashes, stock = [], []
    for m in members:
        for rel in m["data"]:
            key = rel.lower()
            if key in owner:
                clashes.append(f"{rel} ({owner[key]}, {m['slug']})")
            owner[key] = m["slug"]
            if os.path.exists(os.path.join(game, *rel.split("/"))):
                stock.append(f"{m['slug']}: {rel}")
    check(not clashes, "no file path collisions between members", "; ".join(clashes[:8]) + (" ..." if len(clashes) > 8 else ""))
    check(not stock, "no file replaces a stock game file (stock assets are only referenced)", "; ".join(stock[:8]))

    # Ids: unique across members; each member's track folder is its slug
    for kind in ("track", "environment", "event", "layout", "weather list"):
        seen: dict[str, str] = {}
        dup = []
        for m in members:
            for i in sorted(ids_of(m)[kind]):
                if i.lower() in seen:
                    dup.append(f"{i} ({seen[i.lower()]}, {m['slug']})")
                seen[i.lower()] = m["slug"]
        check(not dup, f"no duplicate {kind} ids across members", f"{len(seen)} ids" + (f"; {', '.join(dup)}" if dup else ""))
    wrong = [f"{m['slug']}: {sorted(ids_of(m)['track'])}" for m in members if ids_of(m)["track"] != {m["slug"]}]
    check(not wrong, "each member's tracks live in data/track/<its slug>", "; ".join(wrong))
    if check.failures:  # the merge itself is wrong: don't stage or check scenes
        return {"slug": slug, "title": cfg["title"], "members": len(members), "checksPassed": check.passed,
                "failures": check.failures, "zip": None}

    # Stage the merged mod
    work = os.path.abspath(args.work or os.path.join(REPO, ".local", "tmp", f"bundle-{slug}"))
    stage = os.path.join(work, slug)
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(stage)
    for m in members:
        for rel, info in m["data"].items():
            dest = os.path.join(stage, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with m["zf"].open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)

    # Event, environment and weather files: references resolve; one text per translation key
    import scne_read
    keys: dict[str, tuple[str, str]] = {}
    key_clash, unresolved = [], []
    for m in members:
        for rel in m["data"]:
            if os.path.splitext(rel)[1] not in (".evse", ".envi", ".weli"):
                continue
            payload = scne_read.unbag(open(os.path.join(stage, *rel.split("/")), "rb").read())
            for k, text in translations(payload):
                if k in keys and keys[k][1] != text:
                    key_clash.append(f"{k}: {keys[k][0]} vs {m['slug']}")
                keys.setdefault(k, (m["slug"], text))
            for ref in data_refs(payload):
                if scne_read.resolve(ref, [stage, game]) is None:
                    unresolved.append(f"{rel} -> {ref}")
    check(not key_clash, "no translation key with two texts across members", f"{len(keys)} keys; " + "; ".join(key_clash[:5]))
    check(not unresolved, "event and weather references resolve (in the bundle or the game)", "; ".join(unresolved[:5]))

    # Per-track checks on the merged tree: stock scene limits, driving order, shipped track data (.tcat)
    import check_export
    for m in members:
        track = os.path.join(stage, "data", "track", m["slug"])
        base = os.path.join(track, f"{m['slug']}_base.scne")
        if check(os.path.isfile(base), f"{m['slug']}: base scene {m['slug']}_base.scne"):
            for what, v, lim in scne_read.limits(base, [stage, game]):
                check(v <= lim, f"{m['slug']}: {what} within the stock limit", f"{v:,} (limit {lim:,})")
        titles = {l["event"]: l.get("title") for l in m["report"].get("layouts", [])}
        order = [l["event"] for l in m["report"].get("layouts", [])]
        evdir = os.path.join(stage, "data", "property", "event", m["slug"])
        evs = {f[:-5]: scne_read.unbag(open(os.path.join(evdir, f), "rb").read())
               for f in (os.listdir(evdir) if os.path.isdir(evdir) else []) if f.endswith(".evse")}
        listed = event_order(evs)
        check(not order or listed[:1] == order[:1],
              f"{m['slug']}: the main layout is listed first in the game's event picker",
              f"game order {listed}; main {order[:1]}")
        scenes = sorted(ids_of(m)["layout"], key=lambda s: (order.index(s) if s in order else len(order), s))
        m["layouts"] = []
        for scene in scenes:
            scne = os.path.join(track, scene + ".scne")
            with contextlib.redirect_stdout(check.out):  # check_export prints PASS/FAIL lines; stdout is JSON
                check_export.check_driving_order(scene, scne)
                td = check_export.check_track_data(scene, scne, track)
            lap = (td or {}).get("trackLengthM")
            if not lap:  # e.g. a .tcat with cameras, which tcat.read doesn't decode past: the compiled route
                r = scne_read.read_layout(scne).routes[0]
                mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in r]
                lap = sum(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 for (x0, y0), (x1, y1) in zip(mids, mids[1:] + mids[:1]))
            title = titles.get(scene) or scene
            lname = title[len(m["name"]):].strip() if title.startswith(m["name"]) else title
            m["layouts"].append({"event": scene, "title": title, "name": lname or "Main", "lapM": round(lap, 1)})
    for f in check_export.failures:
        check.failures.append(f)
    check_export.failures.clear()

    # Root files
    n_lay = sum(len(m["layouts"]) for m in members)
    desc = description(cfg, members)
    note = cfg.get("changeNote", "").format(circuits=len(members), layouts=n_lay)
    title = cfg["title"]
    for what, text, lim in (("title", title, LIMITS["titleBytes"]), ("description", desc, LIMITS["descriptionBytes"]),
                            ("change note", note, LIMITS["changeNoteBytes"])):
        n = len(text.encode("utf-8"))
        check(n <= lim, f"Workshop {what} within {lim:,} bytes", f"{n:,} bytes")
    modi = modinfo_bytes(title, desc, note, VISIBILITY[vis_name], published_id)
    back = modinfo_read(modi)
    check(back["title"] == title and back["description"] == desc and back["trailingBytes"] == 0
          and wf_modlist.modinfo_name(modi) == title, "modinfo.modi reads back")
    with open(os.path.join(stage, "modinfo.modi"), "wb") as f:
        f.write(modi)
    sub = cfg.get("previewSubtitle", "{circuits} real circuits, {layouts} layouts").format(circuits=len(members), layouts=n_lay)
    jpg = make_preview(title, sub, [(m["name"], m["preview"]) for m in members], LIMITS["previewBytes"])
    with open(os.path.join(stage, "preview.jpg"), "wb") as f:
        f.write(jpg)
    check(len(jpg) < LIMITS["previewBytes"], "preview.jpg under the Workshop's 1 MB", f"{len(jpg):,} bytes, 1280 x 720")
    with open(os.path.join(stage, "CREDITS.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(credits_txt(cfg, members))
    manifest = {
        "format": "trackgen-bundle/1", "slug": slug, "title": title, "publishedId": published_id,
        "visibility": vis_name,
        "members": [{"slug": m["slug"], "name": m["name"], "zip": os.path.basename(m["zip"]), "zipSha256": m["zipSha256"],
                     "builtAt": m["builtAt"], "layouts": m["layouts"]} for m in members],
    }
    with open(os.path.join(stage, "bundle.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1)

    # Nothing personal in the text we ship
    leaks = []
    for root, _dirs, names in os.walk(stage):
        for n in names:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, stage).replace(os.sep, "/")
            ext = os.path.splitext(n)[1].lower()
            if ext in (".scne", ".bmap", ".jpg"):
                continue
            raw = open(full, "rb").read()
            if ext in (".evse", ".envi", ".weli", ".tcat", ".modi"):
                raw = scne_read.unbag(raw)
            for what, rx in SECRET_PATTERNS.items():
                if hit := rx.search(raw):
                    leaks.append(f"{rel}: {what} {hit.group(0)[:40]!r}")
    check(not leaks, "no IP addresses, Steam IDs, e-mail addresses or passwords in the shipped text", "; ".join(leaks[:5]))

    files = []
    for root, _dirs, names in os.walk(stage):
        for n in names:
            full = os.path.join(root, n)
            files.append((os.path.relpath(full, stage).replace(os.sep, "/"), os.path.getsize(full)))
    files.sort()
    total = sum(s for _, s in files)
    result = {"slug": slug, "title": title, "members": len(members), "layouts": n_lay, "files": len(files),
              "unpackedBytes": total, "checksPassed": check.passed, "failures": check.failures,
              "modinfo": {k: v for k, v in back.items() if k not in ("description",)} | {"descriptionBytes": len(desc.encode())},
              "circuits": [{"name": m["name"], "layouts": [f"{l['name']} {km(l['lapM'])}" for l in m["layouts"]]} for m in members]}
    if check.failures:
        result["zip"] = None
        return result

    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, f"{slug}.zip")
    tmp = zip_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr(zipfile.ZipInfo(f"{slug}/"), "")
        for rel, _size in files:
            z.write(os.path.join(stage, *rel.split("/")), f"{slug}/{rel}")
    os.replace(tmp, zip_path)
    result.update({"zip": zip_path, "zipBytes": os.path.getsize(zip_path), "zipSha256": sha256(zip_path)})
    if args.keep_folder:
        folder = os.path.join(out_dir, slug)
        if os.path.isdir(folder):
            if not os.path.isfile(os.path.join(folder, "bundle.json")):
                raise BundleError(f"{folder} exists and isn't a bundle folder (no bundle.json); not replacing it")
            shutil.rmtree(folder)
        shutil.copytree(stage, folder)
        result["folder"] = folder
    with open(os.path.join(out_dir, f"{slug}.preview.jpg"), "wb") as f:
        f.write(jpg)
    result["preview"] = os.path.join(out_dir, f"{slug}.preview.jpg")
    for m in members:
        m["zf"].close()
    shutil.rmtree(work, ignore_errors=True)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", help="bundle config JSON (tools/bundles/<slug>.json)")
    ap.add_argument("--slug")
    ap.add_argument("--title")
    ap.add_argument("--zip", action="append", help="member zip (repeat); default: the config's tracks in --zip-dir")
    ap.add_argument("--zip-dir")
    ap.add_argument("--out-dir", help="where <slug>.zip goes (default: --zip-dir)")
    ap.add_argument("--game-dir")
    ap.add_argument("--work", help="staging folder (default <repo>/.local/tmp/bundle-<slug>; replaced)")
    ap.add_argument("--keep-folder", action="store_true", help="also leave the unzipped mod in --out-dir/<slug>")
    ap.add_argument("--log", help="write every PASS/FAIL line here (failures also go to stderr)")
    args = ap.parse_args(argv)
    args.log_file = open(args.log, "w", encoding="utf-8") if args.log else None
    try:
        result = build(args)
    except (BundleError, wf_modlist.BagError, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 2
    finally:
        if args.log_file:
            args.log_file.close()
    print(json.dumps(result, indent=1))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
