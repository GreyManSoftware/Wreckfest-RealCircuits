"""Export, build and package a track spec into an installable Wreckfest mod zip.

    <python> pipeline.py --spec spec.json --out DIR [--tcat LAYOUT=FILE ...] [--work DIR] [--keep-work]

Runs inside the worker container (see worker/README.md, "Pipeline"). Steps:
  1. geometry: trackgen_geom.build_track_model(spec); an invalid spec or rejected sketch fails the build
  2. scenes:   blender -b build_scene.py (base + layout .blend files, manifest.json)
  3. export:   blender -b export_scene.py with Toolbox Mz, one .bgo3 per scene (base first)
  4. build:    wf-project, then wf-tool bgeometry per scene; stdout, stderr and Error.log go to the build log;
               known notices (otherway/crossstart "illegal key", Wine XDG noise) are filed under "info"
  5. package:  mod tree mirroring mods/example (bag files, menu images via bimage, modinfo, preview.jpg,
               .tcat uploads), build-report.json, then <slug>.zip that extracts as <slug>/...
Writes DIR/<slug>.zip, DIR/build.log and DIR/build-report.json. Exit code 0 on success, 1 on failure.

Everything happens in a work dir on the container's own filesystem (Wine/bgeometry can't read files on
Docker Desktop bind mounts); only the results are copied to --out.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from common import (BLENDER, ERROR_RE, IGNORED, PIPELINE_VERSION, WORKER_ROOT, BuildError,  # noqa: E402
                    BuildLog, Cancelled, Runner)


def _read(path: str, binary: bool = False):
    with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
        return f.read()


def _versions() -> dict:
    v = {"pipeline": PIPELINE_VERSION}
    try:
        import trackgen_geom
        v["trackgen_geom"] = trackgen_geom.__version__
    except Exception:  # noqa: BLE001
        pass
    for key, cmd in (("blender", [BLENDER[0], "--version"]), ("wine", ["wine", "--version"])):
        try:
            v[key] = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.splitlines()[0].strip()
        except Exception:  # noqa: BLE001
            v[key] = "unknown"
    pinned = os.path.join(os.environ.get("BLENDER_SYSTEM_SCRIPTS", "/opt/blender-scripts"),
                          "addons", "wreckfest_toolbox", "PINNED_COMMIT")
    if os.path.isfile(pinned):
        v["toolbox"] = _read(pinned).strip()
    return v


def geometry(spec: dict, work: str, log: BuildLog) -> tuple[str, object]:
    """Step 1: TrackModel from the spec. Raises BuildError when the spec is invalid."""
    from trackgen_geom import build_track_model

    result = build_track_model(spec)
    for w in result.warnings:
        log.warn(f"geom {w.code}: {w.message}")
    if result.model is None:
        msgs = [f"{e.code}: {e.message}" for e in result.errors]
        msgs += [f"schema {e.path or '/'}: {e.message}" for e in result.rule_result.schema_errors]
        msgs = msgs or ["spec has no main loop"]
        for m in msgs:
            log.error(f"geometry: {m}")
        raise BuildError(("invalid spec: " + "; ".join(msgs))[:500])
    bad = [v for v in result.rule_result.violations if getattr(v, "severity", "error") == "error"]
    for v in result.rule_result.violations:
        (log.error if v in bad else log.warn)(f"rule {v.severity} {v.rule_id}: {v.message}")
    if result.errors or not result.rule_result.valid:
        raise BuildError("invalid spec: " + "; ".join(
            [f"{e.code}" for e in result.errors] + [f"{v.rule_id}: {v.message}" for v in bad])[:500])
    path = os.path.join(work, "model.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(result.model.to_json(indent=1))
    log.line(f"geometry: model built ({len(result.warnings)} warnings)")
    return path, result


def build_scenes(spec_path: str, model_path: str, scenes_dir: str, run: Runner, log: BuildLog) -> dict:
    """Step 2: Blender builder -> .blend files + manifest.json."""
    code, out = run.run("build scenes", BLENDER + ["--python", f"{WORKER_ROOT}/builder/build_scene.py", "--",
                                                    "--spec", spec_path, "--model", model_path, "--out", scenes_dir])
    for line in out.splitlines():
        if line.startswith("  warning: "):
            log.warn("builder " + line[len("  warning: "):])
        elif line.startswith("ERROR"):
            log.error("builder: " + line[6:])
        else:
            log.notice(line)
    if code != 0:
        why = {2: "the sketch was rejected (no model)", 3: "the track doesn't fit within +-2000 m"}.get(code, f"exit {code}")
        raise BuildError(f"scene build failed: {why}")
    manifest = json.loads(_read(os.path.join(scenes_dir, "manifest.json")))
    log.line(f"scenes: {', '.join(manifest['files'].values())}")
    return manifest


def convert_textures(manifest: dict, scenes_dir: str, project: str, run: Runner, log: BuildLog) -> list[str]:
    """The builder's generated maps (TGA under scenes/mod/data/track/<slug>/textures) -> .bmap in the project
    with bimage -auto (which records the data/... path). Returns the mod-relative .bmap paths."""
    slug = manifest["slug"]
    rel_dir = os.path.join("data", "track", slug, "textures")
    src = os.path.join(scenes_dir, "mod", rel_dir)
    out = []
    if not os.path.isdir(src):
        return out
    dst = os.path.join(project, rel_dir)
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        if not name.endswith(".tga"):
            continue
        bmap = os.path.join(dst, name[:-4] + ".bmap")
        code, text = run.run(f"bimage {name}", ["wf-tool", "bimage", "-auto", "-input", os.path.join(src, name),
                                                 "-output", bmap], cwd=src)
        for line in text.splitlines():
            log.notice(line)
        if code != 0 or not os.path.isfile(bmap) or os.path.getsize(bmap) == 0:
            log.error(f"bimage {name}: exit {code}, no .bmap")
            raise BuildError(f"bimage failed for {name}")
        out.append(f"{rel_dir}/{name[:-4]}.bmap".replace(os.sep, "/"))
    log.line(f"textures: {len(out)} generated maps -> .bmap")
    return out


def export_and_build(manifest: dict, scenes_dir: str, project: str, run: Runner, log: BuildLog) -> dict:
    """Steps 3-4: Toolbox export and bgeometry, base first (layouts reference the base)."""
    slug = manifest["slug"]
    track = os.path.join(project, "data", "track", slug)
    os.makedirs(track, exist_ok=True)
    scenes = sorted(manifest["scenes"], key=lambda n: not n.endswith("_base"))
    for scene in scenes:
        bgo = os.path.join(track, f"{scene}.bgo3")
        code, out = run.run(f"export {scene}", BLENDER + ["--python", f"{WORKER_ROOT}/builder/tests/export_scene.py",
                                                           "--", os.path.join(scenes_dir, f"{scene}.blend"), bgo])
        for line in out.splitlines():
            log.notice(line)
        if code != 0 or not os.path.isfile(bgo) or os.path.getsize(bgo) == 0:
            log.error(f"export {scene}: Toolbox export failed (exit {code})")
            raise BuildError(f"Toolbox export failed for {scene}")
        log.line(f"export: {scene}.bgo3 ({os.path.getsize(bgo)} bytes)")
    code, out = run.run("wf-project", ["wf-project", project])
    if code != 0:
        raise BuildError("wf-project failed")
    sizes = {}
    for scene in scenes:
        bgo, scne = os.path.join(track, f"{scene}.bgo3"), os.path.join(track, f"{scene}.scne")
        for f in (os.path.join(track, "Error.log"), os.path.join(project, "Error.log")):
            if os.path.exists(f):
                os.remove(f)
        code, out = run.run(f"bgeometry {scene}", ["wf-tool", "bgeometry", "-v", "-input", bgo, "-output", scne],
                            cwd=track)
        errs = []
        for line in out.splitlines():
            if IGNORED.search(line) or log.notice(line):
                continue
            if ERROR_RE.search(line):
                errs.append(line.strip())
        for f in (os.path.join(track, "Error.log"), os.path.join(project, "Error.log")):
            if os.path.isfile(f):
                text = _read(f, binary=True).decode("utf-8", "replace")
                log.section(f"Error.log after bgeometry {scene}", text)
                for line in text.splitlines():
                    if not IGNORED.search(line) and not log.notice(line):
                        errs.append("Error.log: " + line.strip())
                os.remove(f)
        if code != 0:
            errs.insert(0, f"exit code {code}")
        if not os.path.isfile(scne) or os.path.getsize(scne) == 0:
            errs.append("no .scne written")
        if errs:
            for e in errs:
                log.error(f"bgeometry {scene}: {e}")
            raise BuildError(f"bgeometry {scene}: {errs[0]}"[:500])
        sizes[scene] = os.path.getsize(scne)
        log.line(f"bgeometry: {scene}.scne ({sizes[scene]} bytes)")
    return sizes


def build_mod(spec_path: str, out_dir: str, tcats: dict[str, str] | None = None, work: str | None = None,
              cancel: threading.Event | None = None, keep_work: bool = False, log: BuildLog | None = None) -> dict:
    """Run the whole pipeline. Returns {ok, slug, zip, log, report, error}. Never raises (except Cancelled)."""
    import package

    log = log or BuildLog()
    run = Runner(log, cancel)
    own_work = work is None
    work = work or tempfile.mkdtemp(prefix="trackgen-", dir=os.environ.get("PIPELINE_WORK", os.path.expanduser("~")))
    os.makedirs(work, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    res: dict = {"ok": False, "slug": None, "zip": None, "error": None,
                 "log": os.path.join(out_dir, "build.log"), "report": os.path.join(out_dir, "build-report.json")}
    report: dict = {"pipelineVersion": PIPELINE_VERSION, "startedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        spec_local = os.path.join(work, "spec.json")
        shutil.copyfile(spec_path, spec_local)
        spec = json.loads(_read(spec_local))
        slug = (spec.get("meta") or {}).get("slug") or "track"
        res["slug"] = slug
        log.line(f"build {slug}: spec {spec_path}, work dir {work}")
        report["versions"] = _versions()
        log.section("versions", json.dumps(report["versions"], indent=1))
        model_path, _geom = geometry(spec, work, log)
        scenes_dir = os.path.join(work, "scenes")
        manifest = build_scenes(spec_local, model_path, scenes_dir, run, log)
        project = os.path.join(work, "project")
        textures = convert_textures(manifest, scenes_dir, project, run, log)
        sizes = export_and_build(manifest, scenes_dir, project, run, log)
        report["generatedTextures"] = textures
        report["scenery"] = manifest.get("scenery")
        report["builderTimings"] = manifest.get("timings")
        zip_path, rep = package.package(spec, manifest, _geom.preview_svg, project, sizes, work, out_dir,
                                        tcats or {}, run, log, report)
        res.update(ok=True, zip=zip_path)
        report = rep
        log.line(f"done in {time.time() - t0:.1f} s: {zip_path} ({os.path.getsize(zip_path)} bytes)")
    except Cancelled:
        log.line("cancelled")
        raise
    except BuildError as e:
        res["error"] = str(e)
        log.line(f"FAILED: {e}")
    except Exception as e:  # noqa: BLE001
        res["error"] = f"internal error: {type(e).__name__}: {e}"[:500]
        log.error(res["error"])
        log.section("traceback", traceback.format_exc())
    finally:
        report.setdefault("ok", res["ok"])
        report["ok"] = res["ok"]
        report["error"] = res["error"]
        report["seconds"] = round(time.time() - t0, 1)
        report.setdefault("warnings", log.warnings)
        report["info"] = log.info
        with open(res["report"], "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, indent=1)
        with open(res["log"], "w", encoding="utf-8", newline="\n") as f:
            f.write(log.render())
        if own_work and not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(prog="build-mod", description=__doc__.split("\n\n")[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tcat", action="append", default=[], metavar="LAYOUT=FILE",
                    help="recorded .tcat for a layout (main, main_reverse or the layout scene name)")
    ap.add_argument("--work", help="work dir (default: a temp dir in $HOME, removed afterwards)")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()
    tcats = dict(t.split("=", 1) for t in args.tcat)
    res = build_mod(args.spec, args.out, tcats, args.work, keep_work=args.keep_work)
    print(json.dumps(res, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
