# Comparison renders

Review tool, not part of the build: renders generated base scenes (and the stock fields13 base for comparison)
with Cycles **on the CPU**, using stock textures and stock props, so the visuals can be judged without the game.

```powershell
.\worker\builder\render\Render-Scenes.ps1 -Specs .local\specs\cadwell.json,.local\specs\winkyisland.json -Stock `
    -Out E:\Source\wreckfest\.local\renders -Samples 24 -Cpus 8
```

Writes `<name>-oblique.png`, `<name>-driver.png` and `<name>-horizon.png` (1600×900) per spec, and
`fields13-stock-*` with `-Stock`. The oblique and horizon views frame the terrain (generated and stock alike) and
look out to the horizon, so the far ground, background cards and `#horizon` ring show; `vis = false` collision
walls are hidden, as in game. Pass several specs as an array (`-Specs @('a.json','b.json')`). About
3 minutes for three tracks plus the stock base at 24 samples on 8 threads. Work files go to `.local\render`.

Steps (each can be run on its own):

1. `build` (the image's entrypoint): builder scenes per spec → `.local\render\scenes\<name>`.
2. `prep.py scenes|props` (host Python with `lz4` and `Pillow`; the image has no lz4): decompress the stock
   fields13 base/layout and every prop used (`scenery.json`, the stock subscene list) to `*.raw`.
3. `render_scene.py library` / `stock` / `stocklist` / `needs` (Blender): import each prop once with Toolbox's
   SCNE importer into `props.blend` (collision, shadow and LOD meshes dropped); import the stock base; list the
   textures every material needs.
4. `prep.py textures`: stock `.bmap` → `tex/<data path>.png` (max 1024 px). The generated maps are read from the
   builder's TGA files.
5. `render_scene.py render`: replace each material by an approximation (the #blend mix of three albedos by the
   blend map with the colour map as soft light; alpha from `_c5`/slot 6 for decals, foliage, horizon and cards),
   instance the prop library at every `#xref` subscene, Nishita sky and sun, oblique camera over the terrain and a
   driver-height camera 7 m behind the pole slot (stock: `#startpoint01`); `-DriverAt "name=metres,..."` adds driver views along the main route (`name=route:metres` along another route, e.g. `clubhairpin=club:6`).

Limits: no clutter grass (the game draws grass blades where the clutter mask is green, so real tracks look greener
than these renders, stock and generated alike), no game lighting, weather or fog, nested subscenes inside props
(e.g. the animated spectators in grandstands) are not drawn, and some card textures carry a light haze in their
lower part. Nothing under the game folder is written.
