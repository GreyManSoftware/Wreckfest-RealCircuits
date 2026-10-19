"""Spec weather slots -> stock .weat files, per environment preset.

The schema offers time-of-day slots (sunrise, morning, midday, noon, afternoon, evening, sunset). Each preset
names stock weather families from the game's data/property/weather/ (listed from the mounted install). A slot
takes the first candidate file found in the first family that has one; a family that lacks the slot falls
through to the next family. Only stock paths are referenced: nothing is copied into the mod.
"""
from __future__ import annotations

import os

WEATHER_ROOT = "data/property/weather"

# preset -> families in priority order (folder/prefix under data/property/weather)
PRESET_FAMILIES: dict[str, tuple[str, ...]] = {
    "gravel_pit": ("fields/gravel1", "fields/fields14", "fields/fields13"),
    "farmland": ("fields/fields13", "fields/fields14", "fields/fields08"),
    "forest": ("forest/forest11", "forest/mixed1"),
    "industrial": ("urban/urban06", "urban/urban07", "urban/urban09"),
    "sandpit": ("sandpit/sandpit3", "sandpit/sandpit1", "sandpit/sandpit2"),
    "parkland": ("fields/fields13", "forest/forest11", "fields/fields14"),
    "coastal": ("fields/fields14", "fields/fields13", "fields/gravel1"),
}

# slot -> file suffixes to try in each family, best first
SLOT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "sunrise": ("sunrise1", "sunrise2"),
    "morning": ("morning1", "sunrise2", "sunrise1"),
    "midday": ("midday1", "midday2", "noon1"),
    "noon": ("noon1", "noon2", "midday1"),
    "afternoon": ("afternoon1", "afternoon2"),
    "evening": ("evening1", "sunset2", "sunset1"),
    "sunset": ("sunset1", "sunset2"),
}

# Last resort when no family of the preset has any candidate for a slot.
FALLBACK = "fields/fields14_midday1"


def resolve(environment: str, slots: list[str], game_dir: str | None = None) -> list[dict]:
    """Return [{slot, weat, exact}] in spec order, de-duplicated by .weat path.

    ``exact`` is False when the slot fell back to another time of day or to FALLBACK. With ``game_dir`` the
    files are checked against the install; without it the first candidate of the first family is used.
    """
    families = PRESET_FAMILIES.get(environment, PRESET_FAMILIES["gravel_pit"])
    out, seen = [], set()
    for slot in slots:
        chosen, exact = None, False
        for suffix in SLOT_CANDIDATES.get(slot, ()):
            for fam in families:
                path = f"{WEATHER_ROOT}/{fam}_{suffix}.weat"
                if game_dir is None or os.path.isfile(os.path.join(game_dir, path)):
                    chosen, exact = path, suffix.startswith(slot)
                    break
            if chosen:
                break
        if chosen is None:
            chosen = f"{WEATHER_ROOT}/{FALLBACK}.weat"
        if chosen not in seen:
            seen.add(chosen)
            out.append({"slot": slot, "weat": chosen, "exact": exact})
    return out


def table(game_dir: str | None = None) -> dict[str, dict[str, str]]:
    """The full preset x slot mapping (for the docs and the build report)."""
    n = len(WEATHER_ROOT) + 1
    return {env: {s: resolve(env, [s], game_dir)[0]["weat"][n:] for s in SLOT_CANDIDATES} for env in PRESET_FAMILIES}


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(table(sys.argv[1] if len(sys.argv) > 1 else None), indent=1))
