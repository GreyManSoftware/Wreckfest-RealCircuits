# Wreckfest Real Circuits

Real race circuits for Wreckfest 1, built from open survey data: the line from OpenStreetMap, heights, camber and
widths from public LIDAR. A Blender-based Linux build worker turns each circuit into a checked mod zip. The first
release is Flatshiftcars Track Pack 1: UK & Navarra on the
[Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3826192302).

- Real circuits: [docs/real-tracks.md](docs/real-tracks.md)
- Track pack and Workshop: [docs/workshop-publishing.md](docs/workshop-publishing.md)
- Geometry core and service: [geom/README.md](geom/README.md); build worker: [worker/README.md](worker/README.md);
  importer and helpers: [tools/README.md](tools/README.md)
- Track spec schema: [schema/README.md](schema/README.md)
- Conventions, build and test: [AGENTS.md](AGENTS.md)

## Game files are not included

This repository contains no Wreckfest files. The builder references stock assets by name and runs the game's own
tools (`bgeometry.exe`, `bimage.exe`) from your install, mounted read-only into the build container at runtime. You
need your own copy of Wreckfest (PC, Steam) to build a track.

## Disclaimer

Unofficial fan project. Not affiliated with or endorsed by Bugbear Entertainment, THQ Nordic, MotorSport Vision,
the Circuito de Navarra or any circuit. Circuit names are trademarks of their owners and are used only to say which real track a layout follows.

## Licence

Code and docs: MIT, see [LICENSE](LICENSE). Derived data, sponsor logos and third-party code keep their own terms;
the scope note in `LICENSE` lists them.

## Data and licences

The committed real-circuit data (`tools/real_tracks/`, `tools/real_tracks_data/*.json`, `geom/tests/samples/`) is
derived from the sources below; raw downloads are never committed. Redistributing it, or a mod built from it, needs
the same attribution. Full table: [docs/real-tracks.md](docs/real-tracks.md#data-sources-and-attribution).

| Data | Licence | Attribution |
|---|---|---|
| Circuit layouts, woodland, pits, run-off features | ODbL 1.0 (derived data stays under the ODbL) | © OpenStreetMap contributors |
| Heights and widths, England | Open Government Licence v3.0 | © Environment Agency copyright and/or database right |
| Heights, Wales (Anglesey) | Open Government Licence v3.0 | Contains Natural Resources Wales information © Natural Resources Wales and database right |
| Heights and widths, Navarre | CC BY 4.0 | Servicio proporcionado por el Gobierno de Navarra (IDENA) |

The Linux build image installs [Wreckfest Toolbox Mz](https://github.com/gmazy/wreckfest_toolbox) (MIT) and
Blender (GPL) at build time; see [worker/README.md](worker/README.md).
