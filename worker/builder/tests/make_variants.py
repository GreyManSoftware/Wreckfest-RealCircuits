"""Write the builder test specs: the geom samples plus option variants that cover the other code paths.

usage: python make_variants.py SAMPLES_DIR OUT_DIR
"""
import copy
import json
import os
import sys

VARIANTS = {
    # tarmac, concrete walls, no kerbs, industrial props
    "oval_concrete": ("oval.json", {"meta": {"name": "Concrete Oval", "slug": "oval_concrete"},
                                    "options": {"surface": "tarmac", "walls": "concrete", "kerbs": False,
                                                "environment": "industrial"}}),
    # reverse layout with an alt route and one-way jumps (layout-only jump objects), no walls, sand
    "shortcut_reverse": ("loop_alt_jump.json", {"meta": {"name": "Shortcut Reverse", "slug": "shortcut_reverse"},
                                                "options": {"reverseLayout": True, "walls": "none", "surface": "sand",
                                                            "kerbs": False, "environment": "sandpit"}}),
}


def main() -> None:
    src, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    for name in sorted(os.listdir(src)):
        if name.endswith(".json"):
            with open(os.path.join(src, name), encoding="utf-8") as f:
                spec = json.load(f)
            with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
                json.dump(spec, f, indent=1)
    for vname, (base, patch) in VARIANTS.items():
        with open(os.path.join(src, base), encoding="utf-8") as f:
            spec = json.load(f)
        spec = copy.deepcopy(spec)
        for section, values in patch.items():
            spec[section].update(values)
        with open(os.path.join(out, vname + ".json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(spec, f, indent=1)
    print("specs:", " ".join(sorted(n[:-5] for n in os.listdir(out) if n.endswith(".json"))))


if __name__ == "__main__":
    main()
