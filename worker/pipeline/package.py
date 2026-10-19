"""Assemble the mod tree (mirroring mods/example), write build-report.json and zip it as <slug>/...

Tree for slug S with layouts S_race (and S_race_reverse):
    modinfo.modi, preview.jpg, build-report.json
    data/property/event/S/S.envi, S_race.evse[, S_race_reverse.evse]
    data/property/weather/S/S.weli                     (stock .weat paths only)
    data/track/S/S_base.scne, S_race.scne[, S_race.tcat], ...
    data/track/S/menu/event_S_race_{loading_1920x1200,results_812x162,small_250x116}_raw.bmap
    data/menu/textures/env_S_{large_832x386,small_250x116}_raw.bmap
    data/menu/textures/event_S_race_small_250x116_raw.bmap
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import zipfile

import images
import weather
from common import GAME_DIR, BuildError, BuildLog, Runner

TOOLBOX = os.path.join(os.environ.get("BLENDER_SYSTEM_SCRIPTS", "/opt/blender-scripts"), "addons", "wreckfest_toolbox")
EVENT_STATE_FINAL = 2
GAMEMODE_RACING = 1

UNVERIFIED = [
    "Bag files (.envi, .evse, .weli, modinfo.modi) are written by Toolbox's bagwrite with format dword "
    "01 00 00 00 (uncompressed); stock files use 04 00 00 00 (lz4).",
    "defaultLaps is not written: bagwrite's .evse has no laps field, so the game's default lap count applies.",
    "Event state Final (2) and gamemode Racing (1) in each .evse; one event per layout, id = layout scene name.",
    "Weathers reference stock .weat files of other tracks through the .weli (no .weat is copied).",
    "Menu images: names and power-of-two canvases (content top-left at the named size) copied from the "
    "example mod; bimage -auto chose the format.",
    "Without a recorded .tcat per layout the game makes its own grid and trackside cameras.",
]


def _bagwrite():
    path = os.path.join(TOOLBOX, "addons", "wf_mod_gen", "bagwrite.py")
    spec = importlib.util.spec_from_file_location("bagwrite", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _layout_for_tcat(name: str, slug: str, layouts: list[dict]) -> str | None:
    """API .tcat layout names (main, main_reverse, ...) -> layout scene name."""
    by_key = {lay["key"]: lay["name"] for lay in layouts}  # builder keys: forward | reverse
    n = name.lower()
    if n in by_key.values():
        return n
    alias = {"main": "forward", "race": "forward", "forward": "forward", "main_reverse": "reverse",
             "reverse": "reverse", "race_reverse": "reverse"}.get(n)
    return by_key.get(alias) if alias else None


def layout_title(name: str, lay: dict) -> str:
    """Event title: the track name, the layout's own name when it has one (imported multi-layout tracks, e.g.
    "RealAnglesey Coastal"), and "Reverse" for the reverse layout."""
    return name + (f" {lay['title']}" if lay.get("title") else "") + (" Reverse" if lay["reverse"] else "")


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package(spec: dict, manifest: dict, preview_svg: str, project: str, sizes: dict, work: str, out_dir: str,
            tcats: dict[str, str], run: Runner, log: BuildLog, report: dict) -> tuple[str, dict]:
    bw = _bagwrite()
    slug = manifest["slug"]
    meta, opts = spec.get("meta", {}), spec.get("options", {})
    name = meta.get("name") or slug
    desc = meta.get("description") or f"Generated track: {opts.get('lapLengthKm', '?')} km, {opts.get('surface', '')}"
    layouts = manifest["layouts"]
    mod = os.path.join(work, "mod", slug)
    shutil.rmtree(mod, ignore_errors=True)

    def p(*parts: str) -> str:
        path = os.path.join(mod, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # Scenes (no .bgo3/.blend sources: the game only needs .scne)
    track_rel = ("data", "track", slug)
    src_track = os.path.join(project, *track_rel)
    for scene in sizes:
        shutil.copyfile(os.path.join(src_track, f"{scene}.scne"), p(*track_rel, f"{scene}.scne"))
    # Generated blend/colour/clutter maps (bimage'd by the pipeline); the materials reference them by mod path
    # (loop variable must not be `name`: that is the mod's display name, used below for modinfo/envi/evse)
    tex_dir = os.path.join(src_track, "textures")
    if os.path.isdir(tex_dir):
        for tex in sorted(os.listdir(tex_dir)):
            if tex.endswith(".bmap"):
                shutil.copyfile(os.path.join(tex_dir, tex), p(*track_rel, "textures", tex))
    # The scenery placement manifest (for the web preview), beside the zip
    scenery = os.path.join(work, "scenes", "scenery.json")
    if os.path.isfile(scenery):
        shutil.copyfile(scenery, os.path.join(out_dir, "scenery.json"))

    # .tcat uploads beside their layout .scne
    tcat_report = {}
    for api_name, path in sorted(tcats.items()):
        target = _layout_for_tcat(api_name, slug, layouts)
        if not target:
            log.warn(f"tcat '{api_name}' doesn't match a layout ({', '.join(l['name'] for l in layouts)}); skipped")
            continue
        shutil.copyfile(path, p(*track_rel, f"{target}.tcat"))
        tcat_report[target] = api_name
        log.line(f"tcat: {api_name} -> {target}.tcat")

    # Weather list
    env = opts.get("environment", "gravel_pit")
    weathers = weather.resolve(env, opts.get("weathers") or ["midday"], GAME_DIR if os.path.isdir(GAME_DIR) else None)
    for w in weathers:
        if not w["exact"]:
            log.warn(f"weather '{w['slot']}' has no exact stock match for {env}; using {w['weat']}")
    weli_rel = f"data/property/weather/{slug}/{slug}.weli"
    f = bw.BagWrite()
    f.weli_file([w["weat"] for w in weathers])
    f.save(p(*weli_rel.split("/")))

    # Environment (event group) and one event per layout
    f = bw.BagWrite()
    f.envi_file(title=name, desc=desc, location="")
    f.save(p("data", "property", "event", slug, f"{slug}.envi"))
    events = []
    for lay in layouts:
        title = layout_title(name, lay)
        scne = f"data/track/{slug}/{lay['name']}.scne"
        f = bw.BagWrite()
        f.evse_file(title=title, desc=desc, state=EVENT_STATE_FINAL, gamemode=GAMEMODE_RACING, scne=scne,
                    weli=weli_rel)
        f.save(p("data", "property", "event", slug, f"{lay['name']}.evse"))
        events.append({"event": lay["name"], "title": title, "scne": scne, "reverse": lay["reverse"],
                       "scneBytes": sizes.get(lay["name"]), "tcat": tcat_report.get(lay["name"])})

    f = bw.BagWrite()
    f.modi_file(name=name, preview="preview.jpg", desc=desc, tagbits=8)  # visibility 2 = private; 8 = track
    f.save(p("modinfo.modi"))

    # Images: TGA sources in a staging project (bimage stores the data/... path), .bmap into the mod
    pv = images.render_svg(preview_svg)
    sub = f"{opts.get('lapLengthKm', '?')} km  |  {env.replace('_', ' ')}  |  {opts.get('surface', '')}"
    stage = os.path.join(work, "images")
    jobs = []  # (relpath without extension, image, alpha)
    for lay in layouts:
        lsub = ("Reverse  |  " if lay["reverse"] else "") + sub
        title = layout_title(name, lay)
        base = f"data/track/{slug}/menu/event_{lay['name']}"
        jobs.append((f"{base}_loading_1920x1200_raw", images.card(1920, 1200, pv, title, lsub), False))
        jobs.append((f"{base}_results_812x162_raw", images.card(812, 162, pv, title, lsub), True))
        jobs.append((f"{base}_small_250x116_raw", images.card(250, 116, pv, title, lsub), True))
        jobs.append((f"data/menu/textures/event_{lay['name']}_small_250x116_raw",
                     images.card(250, 116, pv, title, lsub), True))
    jobs.append((f"data/menu/textures/env_{slug}_large_832x386_raw", images.card(832, 386, pv, name, sub), True))
    jobs.append((f"data/menu/textures/env_{slug}_small_250x116_raw", images.card(250, 116, pv, name, sub), True))
    for rel, img, alpha in jobs:
        tga = images.save_tga(img, os.path.join(stage, *rel.split("/")) + ".tga", alpha)
        out = p(*rel.split("/")) + ".bmap"
        code, text = run.run(f"bimage {os.path.basename(rel)}", ["wf-tool", "bimage", "-auto", "-input", tga,
                                                                  "-output", out], cwd=stage)
        for line in text.splitlines():
            log.notice(line)
        if code != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
            log.error(f"bimage {rel}: exit {code}, no .bmap")
            raise BuildError(f"bimage failed for {os.path.basename(rel)}")
    log.line(f"images: {len(jobs)} .bmap files")
    images.save_preview_jpg(pv, name, sub, p("preview.jpg"))

    # Report (inside the zip and beside it)
    files = []
    for root, _dirs, names in os.walk(mod):
        for n in names:
            full = os.path.join(root, n)
            files.append({"path": os.path.relpath(full, mod).replace(os.sep, "/"), "bytes": os.path.getsize(full)})
    files.sort(key=lambda x: x["path"])
    report.update({
        "slug": slug,
        "name": name,
        "specSha256": manifest.get("specSha256"),
        "builderVersion": manifest.get("builderVersion"),
        "environment": env,
        "defaultLaps": opts.get("defaultLaps"),
        "layouts": events,
        "weathers": weathers,
        "transform": manifest.get("transform"),
        "tcatsIncluded": tcat_report,
        "warnings": log.warnings,
        "unverifiedInGame": UNVERIFIED + [w for w in manifest.get("warnings", []) if w.startswith("unverified")],
        "files": files,
        "finishedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    with open(p("build-report.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=1)

    zip_path = os.path.join(out_dir, f"{slug}.zip")
    tmp = zip_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr(zipfile.ZipInfo(f"{slug}/"), "")
        for rel in [x["path"] for x in files] + ["build-report.json"]:
            z.write(os.path.join(mod, *rel.split("/")), f"{slug}/{rel}")
    os.replace(tmp, zip_path)
    report["zipSha256"] = _sha(zip_path)
    report["zipBytes"] = os.path.getsize(zip_path)
    return zip_path, report
