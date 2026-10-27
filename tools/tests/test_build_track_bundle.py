"""tools/build_track_bundle.py: modinfo format and the merge checks that need no built scenes (fast, synthetic zips).

    py -m pytest -q tools/tests/test_build_track_bundle.py

The scene checks (scne_read limits, driving order, .tcat) are check_export's own and run on real zips in
tools/Build-TrackBundle.ps1; the builder tests cover them on every sample.
"""
from __future__ import annotations

import glob
import json
import os
import struct
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build_track_bundle as b  # noqa: E402
import wf_modlist  # noqa: E402

WORKSHOP = r"D:\SteamLibrary\steamapps\workshop\content\228380"
CFG = {"slug": "test_pack", "title": "Test Pack", "credits": ["© OpenStreetMap contributors, ODbL"],
       "disclaimer": "Unofficial."}


def test_modinfo_matches_bagedit_format():
    data = b.modinfo_bytes("Flatshiftcars Track Pack 1: UK & Navarra", "Desc © ä\n[b]x[/b]", "First release.", b.VISIBILITY["public"], 3_456_789_012)
    bag = wf_modlist.bag_decode(data)
    assert (bag.fmt, bag.tag, bag.version) == (4, b"idom", 0)  # lz4, like every Workshop item's modinfo.modi
    m = b.modinfo_read(data)
    assert m == {"title": "Flatshiftcars Track Pack 1: UK & Navarra", "preview": "preview.jpg", "description": "Desc © ä\n[b]x[/b]",
                 "changeNote": "First release.", "visibility": 0, "tagBits": 8, "publishedId": 3_456_789_012,
                 "trailingBytes": 0}
    assert wf_modlist.modinfo_name(data) == "Flatshiftcars Track Pack 1: UK & Navarra"


@pytest.mark.skipif(not os.path.isdir(WORKSHOP), reason="no Workshop items on this machine")
def test_reader_parses_published_workshop_items():
    files = glob.glob(os.path.join(WORKSHOP, "*", "modinfo.modi"))
    for f in files:
        m = b.modinfo_read(open(f, "rb").read())
        assert m["trailingBytes"] == 0 and m["publishedId"] == int(os.path.basename(os.path.dirname(f))), f


def test_translations_and_refs():
    title, key = "Brands Hatch Indy", "EVENT_TITLE_0123456789_17"
    payload = (b"esve" + struct.pack("<iii", 17, 2, 0) + b"nart" + struct.pack("<ii", 2, 1)
               + struct.pack("<I", len(key)) + key.encode() + struct.pack("<I", len(title)) + title.encode()
               + struct.pack("<I", 40) + b"data/track/brandshatch/brandshatch_indy.scne")
    assert b.translations(payload) == [(key, title)]
    assert b.data_refs(payload) == {"data/track/brandshatch/brandshatch_indy.scne"}


@pytest.mark.parametrize("states, want", [
    ({"anglesey_club": 2, "anglesey_race": 2, "anglesey_coastal": 2}, ["anglesey_club", "anglesey_coastal", "anglesey_race"]),
    ({"anglesey_club": 4, "anglesey_race": 2, "anglesey_coastal": 4}, ["anglesey_race", "anglesey_club", "anglesey_coastal"]),
    ({"x_b": 1, "x_a": 4, "x_c": 2, "x_d": 3}, ["x_c", "x_b", "x_a", "x_d"]),  # Final, Developer, Mod, others
])
def test_event_order_follows_the_games_sort(states, want):
    assert b.event_order({e: struct.pack("<ii", s, 0) for e, s in states.items()}) == want


def _member(tmp_path, slug, files):
    path = tmp_path / f"{slug}.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{slug}/modinfo.modi", b.modinfo_bytes(slug.title(), "", "", 2, 0))
        z.writestr(f"{slug}/build-report.json", json.dumps({"layouts": []}))
        for name in files:
            z.writestr(f"{slug}/{name}", b"x")
    return str(path)


def _run(tmp_path, zips, game_files=()):
    game = tmp_path / "game"
    (game / "data").mkdir(parents=True, exist_ok=True)
    for g in game_files:
        (game / g).parent.mkdir(parents=True, exist_ok=True)
        (game / g).write_bytes(b"stock")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(CFG), encoding="utf-8")
    args = b.argparse.Namespace(config=str(cfg), slug=None, title=None, zip=zips, zip_dir=None, game_dir=str(game),
                                out_dir=str(tmp_path / "out"), work=str(tmp_path / "work"), keep_folder=False,
                                log_file=None)
    return b.build(args)


@pytest.mark.parametrize("files_a,files_b,game_files,want", [
    # the same path in two members (case-insensitive, as on Windows)
    (["data/track/a/a_race.scne", "data/menu/textures/shared.bmap"],
     ["data/track/b/b_race.scne", "data/menu/textures/SHARED.bmap"], [], "no file path collisions"),
    # an event id in two members, in different folders
    (["data/track/a/a_race.scne", "data/property/event/a/same_race.evse"],
     ["data/track/b/b_race.scne", "data/property/event/b/same_race.evse"], [], "no duplicate event ids"),
    # a layout id in two members
    (["data/track/a/x_club.scne"], ["data/track/b/x_club.scne"], [], "no duplicate layout ids"),
    # a member file that would replace a stock game file
    (["data/track/a/a_race.scne", "data/art/objects/tree.scne"], ["data/track/b/b_race.scne"],
     ["data/art/objects/tree.scne"], "no file replaces a stock game file"),
    # a file type a generated mod doesn't ship, and a track outside data/track/<slug>
    (["data/track/a/a_race.scne", "data/track/a/a_base.blend"], ["data/track/b/b_race.scne"], [], "zip holds only a generated mod"),
    (["data/track/a/a_race.scne", "data/track/zz/zz_race.scne"], ["data/track/b/b_race.scne"], [], "data/track/<its slug>"),
])
def test_merge_failures_write_no_zip(tmp_path, files_a, files_b, game_files, want):
    r = _run(tmp_path, [_member(tmp_path, "a", files_a), _member(tmp_path, "b", files_b)], game_files)
    assert r["zip"] is None and any(want in f for f in r["failures"]), r["failures"]
    assert not os.path.exists(tmp_path / "out" / "test_pack.zip")


def test_unsafe_entry_fails(tmp_path):
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a/data/track/a/a_race.scne", b"x")
        z.writestr("a/../evil.scne", b"x")
    r = _run(tmp_path, [str(path), _member(tmp_path, "b", ["data/track/b/b_race.scne"])])
    assert r["zip"] is None and any("unsafe entry" in f for f in r["failures"]), r["failures"]


def test_bad_bundle_slug_is_an_input_error(tmp_path):
    with pytest.raises(b.BundleError):
        b.build(b.argparse.Namespace(config=None, slug="Bad-Slug", title="x", zip=["a.zip"], zip_dir=None,
                                     game_dir=None, out_dir=None, work=None, keep_folder=False, log_file=None))
