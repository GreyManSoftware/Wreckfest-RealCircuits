<#
.SYNOPSIS
    Run the Blender builder tests in the worker container (CPU only, game folder mounted read-only).

.EXAMPLE
    .\worker\builder\run-builder-tests.ps1                      # all specs + determinism check
    .\worker\builder\run-builder-tests.ps1 -Specs oval,figure8_bridge
    .\worker\builder\run-builder-tests.ps1 -Build               # rebuild the image first

.NOTES
    Mounts worker\builder, worker\blender and worker\scripts from the repo (no rebuild needed for builder
    changes; trackgen_geom comes from the image, so rebuild after geom changes) and geom\tests\samples
    read-only. Outputs go to .local\builder-out (gitignored). Exit code = number of failures.
#>
[CmdletBinding()]
param(
    [string[]] $Specs = @(),
    [string] $WreckfestDir = $(if ($env:WRECKFEST_DIR) { $env:WRECKFEST_DIR } else { 'D:\SteamLibrary\steamapps\common\Wreckfest' }),
    [string] $Image = 'trackgen-worker:dev',
    [double] $Cpus = 8,
    [string] $Memory = '6g',
    [string] $Out = '',
    [string] $Determinism = 'oval',
    [switch] $Build
)
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Out) { $Out = Join-Path $repo '.local\builder-out' }
if (-not (Test-Path (Join-Path $WreckfestDir 'tools\bgeometry.exe'))) {
    throw "bgeometry.exe not found under '$WreckfestDir\tools'. Pass -WreckfestDir or set WRECKFEST_DIR."
}
New-Item -ItemType Directory -Force $Out | Out-Null

if ($Build) {
    docker build -f (Join-Path $repo 'worker\Dockerfile') -t $Image $repo
    if ($LASTEXITCODE) { throw "docker build failed ($LASTEXITCODE)" }
}

$dockerArgs = @(
    'run', '--rm', '--cpus', $Cpus, '--memory', $Memory,
    '--mount', "type=bind,source=$WreckfestDir,target=/wreckfest,readonly",
    '--mount', "type=bind,source=$(Join-Path $repo 'geom\tests\samples'),target=/samples,readonly",
    '--mount', "type=bind,source=$Out,target=/out",
    '-e', "BUILDER_DETERMINISM=$Determinism"
)
foreach ($dir in 'builder', 'blender', 'scripts') {
    $dockerArgs += @('--mount', "type=bind,source=$(Join-Path $repo "worker\$dir"),target=/opt/worker/$dir,readonly")
}
$dockerArgs += @($Image, 'bash', '/opt/worker/builder/tests/run_builder_tests.sh') + $Specs

Write-Host "docker $($dockerArgs -join ' ')"
& docker @dockerArgs
exit $LASTEXITCODE
