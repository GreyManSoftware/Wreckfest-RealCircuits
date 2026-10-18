"""Write sample Bag files with Toolbox Mz's wf_mod_gen/bagwrite.py and check their headers.

usage: python smoke_bagwrite.py OUT_DIR
Runs in plain Python (Blender's bundled interpreter is fine); bagwrite.py has no bpy dependency, so it is
loaded straight from the add-on folder rather than through the add-on package (whose __init__ imports bpy).

Header check: <u32 format> + <4-byte tag, reversed>. bagwrite writes format 1 (uncompressed); stock game
files use 4. Both are accepted here.
"""
import importlib.util
import os
import struct
import sys

ADDON = os.path.join(
    os.environ.get("BLENDER_SYSTEM_SCRIPTS", "/opt/blender-scripts"), "addons", "wreckfest_toolbox"
)


def load_bagwrite():
    path = os.path.join(ADDON, "addons", "wf_mod_gen", "bagwrite.py")
    spec = importlib.util.spec_from_file_location("bagwrite", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(out_dir: str) -> int:
    bagwrite = load_bagwrite()
    group, event = "smoketrack", "smoketrack_derby"
    files = {}

    f = bagwrite.BagWrite()
    f.envi_file(title="Smoke Track", desc="Toolchain smoke test", location="Docker")
    files["envi"] = (os.path.join(out_dir, "data", "property", "event", group, group + ".envi"), f, "envi")

    f = bagwrite.BagWrite()
    f.evse_file(
        title="Smoke Derby",
        desc="Toolchain smoke test",
        state=4,
        gamemode=4,
        scne=f"data/track/{group}/{group}.scne",
        weli=f"data/property/weather/{group}/{group}.weli",
    )
    files["evse"] = (os.path.join(out_dir, "data", "property", "event", group, event + ".evse"), f, "evse")

    f = bagwrite.BagWrite()
    f.weli_file(["data/property/weather/fields/fields14_noon1.weat"])
    files["weli"] = (os.path.join(out_dir, "data", "property", "weather", group, group + ".weli"), f, "weli")

    f = bagwrite.BagWrite()
    f.modi_file(name="Smoke Track", desc="Toolchain smoke test", tagbits=8)
    files["modi"] = (os.path.join(out_dir, "modinfo.modi"), f, "modi")

    failures = 0
    for kind, (path, bag, tag) in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bag.save(path)
        with open(path, "rb") as fh:
            data = fh.read()
        fmt = struct.unpack_from("<I", data, 0)[0] if len(data) >= 8 else None
        got_tag = data[4:8]
        ok = len(data) > 8 and fmt in (1, 4) and got_tag == tag[::-1].encode()
        failures += not ok
        print(
            f"{'PASS' if ok else 'FAIL'}: {kind} {path} {len(data)} bytes, "
            f"head {data[:12].hex(' ')} (format {fmt}, tag {got_tag!r} == reversed {tag!r})"
        )
    return failures


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
