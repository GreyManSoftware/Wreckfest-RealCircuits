# Publishing the track bundle to the Steam Workshop

> **Don't publish yet.** Wait until the intro-camera fix (the intro camera starts under the ground on every
> generated track; the `.tcat` files gain generated cameras) and the missing Oulton Park chicane are fixed, all
> eight per-circuit zips are rebuilt, and the bundle is rebuilt and reinstalled from them (steps 1 and 2).

One public Workshop item, **"Flatshiftcars Track Pack 1: UK & Navarra"** (folder and slug `msvanglesey`), holds all eight real
circuits: Cadwell Park, Brands Hatch, Oulton Park, Snetterton, Donington Park, Bedford Autodrome, Circuito de
Navarra and Anglesey (24 layouts). Chris publishes it himself with BagEdit's **Mod Publish**, which runs the game's
`tools\workshop\Uploader.exe`. Never publish from a script and never automatically (AGENTS.md).

| File | What |
|---|---|
| `tools\bundles\msvanglesey.json` | Title, **Published Id**, visibility, member list, description parts, credits |
| `tools\Build-TrackBundle.ps1` (+ `tools\build_track_bundle.py`) | Builds `E:\WreckfestMods\msvanglesey.zip` from the per-circuit zips and checks it |
| `tools\Install-TrackMod.ps1 -Bundle` | Installs it into the game's `mods\msvanglesey` and switches the eight single-circuit mods off |

## What the bundle contains and checks

Every member's `data\` tree unchanged, plus its own root files:

- `modinfo.modi`, in the format BagEdit's Mod Publish writes (lz4 bag `idom`):
  - Title: "Flatshiftcars Track Pack 1: UK & Navarra" (Workshop limit 128 bytes).
  - Preview file: `preview.jpg`.
  - Description: Steam BBCode listing every circuit and layout with its lap length as built, how to enable it,
    that servers and players need it, and the credits (limit 8,000 bytes; about 2,100 now).
  - Latest change note: "First release: 8 circuits, 24 layouts."
  - Visibility 0 = **Public**. The field exists: 0 public, 1 friends, 2 private/hidden, which is BagEdit's default
    and what our per-circuit mods carry. Every published Workshop item we have reads 0.
  - Tag bits 8 = track.
  - Published Id 0, from the config.
- `preview.jpg`: 1280 × 720, a collage of the eight circuit maps with their names, about 100 KB (limit 1 MB).
  A copy is saved beside the zip as `E:\WreckfestMods\msvanglesey.preview.jpg`.
- `CREDITS.txt` with the same credits:
  - © OpenStreetMap contributors (ODbL);
  - Environment Agency LIDAR (OGL v3);
  - Natural Resources Wales / Welsh Government LIDAR (OGL v3);
  - IGN/CNIG PNOA MDT05 (CC BY 4.0);
  - the disclaimer: unofficial fan-made, not affiliated with or endorsed by MotorSport Vision or the circuits.
  - No logos.
- `bundle.json`: the members and their layouts, used by `Install-TrackMod.ps1 -Bundle`.

The build refuses to write a zip on any of these:

- a file path two members share;
- a duplicate track, environment, event, layout or weather-list id;
- a translation key with two texts;
- a file that would replace a stock game file (stock assets are only referenced);
- an event or weather reference that doesn't resolve;
- `scne_read --limits` on any base scene;
- `check_export.check_driving_order` or `check_export.check_track_data` (the shipped `.tcat`) on any layout;
- an IP address, Steam ID, e-mail address or `password=` in the shipped text;
- a Workshop limit.

The PASS/FAIL lines are in `.local\bundles\msvanglesey-checks.log`.

## First publish (Chris)

0. **Rebuild the bundle** after any per-circuit zip changes (re-runnable):

   ```powershell
   cd E:\Source\wreckfest
   .\tools\Build-TrackBundle.ps1
   ```

1. **Install it into the game's mods folder, with the game closed.** Bugbear: publish from the game's `mods` folder,
   never from the Workshop folder.

   ```powershell
   .\tools\Install-TrackMod.ps1 -Zip E:\WreckfestMods\msvanglesey.zip -Bundle -Force
   .\tools\Install-TrackMod.ps1 -ListMods      # Flatshiftcars Track Pack 1: yes; the eight single-circuit mods: no
   ```

   This creates `D:\SteamLibrary\steamapps\common\Wreckfest\mods\msvanglesey`. Optionally start the game once and
   drive a layout or two.
2. **Open BagEdit:** `D:\SteamLibrary\steamapps\common\Wreckfest\BagEdit\BagEditCommunity.exe`.
3. **Open any file in the bundle**, e.g. `mods\msvanglesey\data\property\event\brandshatch\brandshatch_indy.evse`.
4. **Right-click it → Mod Publish.** It reads the existing `mods\msvanglesey\modinfo.modi`.
5. **Check the fields:**
   - Title: Flatshiftcars Track Pack 1: UK & Navarra.
   - Preview: `preview.jpg`.
   - Description and Latest Change Note: as above.
   - Visibility: Public.
   - Published Id: 0.
   - Track tag set.

   Then click **Publish to Workshop**. The upload is about 56 MB compressed and can take a while.
6. **Accept the Steam Workshop Legal Agreement** if Steam asks. Until it's accepted the item stays hidden whatever
   the visibility says. The item's Workshop page opens when the upload has finished.
7. **Record the Published Id.** Mod Publish writes the new id into `mods\msvanglesey\modinfo.modi`. It's also the
   number in the Workshop page's URL (`...filedetails/?id=<id>`). Put it in `tools\bundles\msvanglesey.json`
   (`"publishedId": <id>`) and commit it.

   Do this **before** rebuilding or reinstalling. A reinstall replaces the folder with the zip's `modinfo.modi`, and
   with Published Id 0 the next publish would create a second Workshop item.

After publishing, Steam subscribes you and the game shows the Workshop copy as **another** mod entry (guide, step 7).
Keep only one copy enabled, the local `mods/msvanglesey` or the Workshop one. Both are the same tracks and would
clash. `-ListMods` shows both. On 2026-10-26 both were on (Workshop v1 and a newer local build), and the AI drove off
the road on every circuit whose world the newer build had moved. `Install-TrackMod.ps1 -Bundle` now switches the
Workshop copy (`publishedId`) off when it installs the local one.

## Updating the published item

1. Rebuild the per-circuit zips as needed, then:
   - set `changeNote` in `tools\bundles\msvanglesey.json` to what changed (it's the Workshop's change note);
   - check that `publishedId` is the real id;
   - run `.\tools\Build-TrackBundle.ps1`.
2. With the game closed, run `.\tools\Install-TrackMod.ps1 -Zip E:\WreckfestMods\msvanglesey.zip -Bundle -Force`.
3. In BagEdit, right-click a file in `mods\msvanglesey` → Mod Publish. Leave the **Published Id** as it is (not 0)
   and check the Latest Change Note, then **Publish to Workshop**.

**The Workshop page's title, description and visibility are overwritten from `modinfo.modi` on every publish.**
Edit them in `tools\bundles\msvanglesey.json` (or in the Mod Publish dialog, then copy the change back into the
config), never only on the Steam page.

## Notes

- The installed folder holds the installer's `.trackgen` marker. It's one line: "Installed by
  tools/Install-TrackMod.ps1 on <time> from msvanglesey.zip", with nothing personal. The uploader may ship it, which
  is harmless. Leave it in place: `-Force` and `-Uninstall` need it.
- The size is reasonable: 263 files, 75.5 MB unpacked, 56.4 MB zipped. It holds only generated `.scne`, `.tcat`,
  `.bmap`, `.evse`, `.envi` and `.weli` files, with no stock game files, Blender sources or build reports.
- To go back to the single-circuit mods, run `.\tools\Install-TrackMod.ps1 -Uninstall -Slug msvanglesey -Bundle`.
  It switches the bundle off, removes it and switches the installed members back on.

## Dedicated server (not changed; needs Chris's approval)

The server runs from the same install (`D:\SteamLibrary\steamapps\common\Wreckfest`), so `mods\msvanglesey` is
already in its mods folder. A server on another machine needs the folder copied into its `mods\`. To host the
tracks:

- add `mods=msvanglesey` to `server_config.cfg`;
- add the events (`el_add=cadwellpark_race`, ...) to the rotation;
- add the eight circuits to `Start-WreckfestServer.ps1`'s `$circuitMap`;
- update its `$expected` counts.

`.\tools\Install-TrackMod.ps1 -Zip E:\WreckfestMods\msvanglesey.zip -Bundle -Force -Server` prints these lines
(it reinstalls the bundle too, game closed; the server files are never written). Every player needs the same mod enabled: the Workshop item, or the local copy, not both. Don't
list the single-circuit mods on the server as well.
