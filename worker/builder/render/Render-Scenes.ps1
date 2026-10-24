<#
.SYNOPSIS
    Comparison renders (Cycles, CPU only) of generated tracks and optionally a stock base, for reviewing visuals
    without the game. See worker/builder/render/README.md.

.EXAMPLE
    .\worker\builder\render\Render-Scenes.ps1 -Specs .local\specs\cadwell.json,.local\specs\winkyisland.json -Stock -Out E:\Source\wreckfest\.local\renders

.NOTES
    Needs a host Python with lz4 + Pillow for prep.py (-HostPython). Work files go to .local\render (gitignored).
    CPU only: --cpus and RENDER_THREADS default to 8. Never touches the game folder except to read it.
#>
[CmdletBinding()]
param(
    [string[]] $Specs = @(),
    [switch] $Stock,
    [string] $Out = '',
    [int] $Samples = 16,
    [int] $Cpus = 8,
    [string] $DriverAt = '',
    [switch] $Intro,
    [string] $Image = 'trackgen-worker:dev',
    [string] $HostPython = 'E:\Source\wreckfest\.local\visual\venv\Scripts\python.exe',
    [string] $WreckfestDir = $(if ($env:WRECKFEST_DIR) { $env:WRECKFEST_DIR } else { 'D:\SteamLibrary\steamapps\common\Wreckfest' })
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$R = Join-Path $repo '.local\render'
if (-not $Out) { $Out = Join-Path $repo '.local\renders' }
New-Item -ItemType Directory -Force $R, (Join-Path $R 'scenes'), (Join-Path $R 'specs'), (Join-Path $R 'out'), $Out | Out-Null
$prep = Join-Path $PSScriptRoot 'prep.py'
$base = @('run', '--rm', '--cpus', $Cpus, '--memory', '10g', '-e', "RENDER_THREADS=$Cpus", '-e', "RENDER_DRIVER_AT=$DriverAt", '-e', "RENDER_INTRO=$(if ($Intro) { '1' } else { '' })",
    '--mount', "type=bind,source=$WreckfestDir,target=/wreckfest,readonly",
    '--mount', "type=bind,source=$R,target=/r",
    '--mount', "type=bind,source=$(Join-Path $repo 'worker\builder'),target=/opt/worker/builder,readonly")
$blender = @($Image, 'blender', '-b', '--factory-startup', '--python', '/opt/worker/builder/render/render_scene.py', '--')

$slugs = @()
foreach ($s in $Specs) {
    $name = [IO.Path]::GetFileNameWithoutExtension($s)
    Copy-Item $s (Join-Path $R "specs\$name.json") -Force
    & docker @base $Image build --spec "/r/specs/$name.json" --out "/r/scenes/$name" | Select-String '^built|ERROR'
    if ($LASTEXITCODE) { throw "build $name failed" }
    $slugs += $name
}
$sceneries = $slugs | ForEach-Object { Join-Path $R "scenes\$_\scenery.json" }
$propLists = @($sceneries)
if ($Stock) {
    & $HostPython $prep scenes $R data/art/levels/fields/fields13_1/fields13_base.scne data/art/levels/fields/fields13_1/fields13_1.scne
    & docker @base @blender stock /r /r/raw/fields13_base.scne.raw /r/raw/fields13_1.scne.raw | Out-Null
    & docker @base @blender stocklist /r | Out-Null
    $propLists += (Join-Path $R 'stock_props.txt')
}
& $HostPython $prep props $R @propLists
& docker @base @blender library /r | Out-Null
& docker @base @blender needs /r @($slugs | ForEach-Object { "/r/scenes/$_" }) | Select-String 'textures'
& $HostPython $prep textures $R (Join-Path $R 'needed.json') | Select-Object -Last 1
$targets = @($slugs | ForEach-Object { , @("/r/scenes/$_", $_) })
if ($Stock) { $targets += , @('stock', 'fields13-stock') }
foreach ($t in $targets) {
    & docker @base @blender render /r $t[0] "/r/out/$($t[1])" $Samples | Select-String 'props:|RENDERED|Error'
    Get-ChildItem (Join-Path $R 'out') -Filter "$($t[1])-*.png" | Copy-Item -Destination $Out -Force
}
Get-ChildItem $Out -Filter *.png | Select-Object Name, Length
