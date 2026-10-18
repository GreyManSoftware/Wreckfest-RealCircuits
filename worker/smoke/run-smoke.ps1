<#
.SYNOPSIS
    Run the worker toolchain smoke tests in Docker with the Wreckfest install mounted read-only.

.EXAMPLE
    .\worker\smoke\run-smoke.ps1                 # all tests (a b c d)
    .\worker\smoke\run-smoke.ps1 -Tests b,c      # some tests
    .\worker\smoke\run-smoke.ps1 -Build          # rebuild the image first

.NOTES
    CPU only (no --gpus) and limited to -Cpus (default 4). The game folder is mounted read-only at /wreckfest
    and inputs are copied into the container before use. worker\scripts, worker\blender and worker\smoke are mounted from the repo so
    they can be edited without rebuilding (pass -UseBakedScripts to test the copies baked into the image).
    Exit code = number of failed tests.
#>
[CmdletBinding()]
param(
    [string[]] $Tests = @(),
    [string] $WreckfestDir = $(if ($env:WRECKFEST_DIR) { $env:WRECKFEST_DIR } else { 'D:\SteamLibrary\steamapps\common\Wreckfest' }),
    [string] $Image = 'trackgen-worker:dev',
    [double] $Cpus = 4,
    [string] $Memory = '4g',
    [switch] $Build,
    [switch] $UseBakedScripts
)
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not (Test-Path (Join-Path $WreckfestDir 'tools\bgeometry.exe'))) {
    throw "bgeometry.exe not found under '$WreckfestDir\tools'. Pass -WreckfestDir or set WRECKFEST_DIR."
}

if ($Build) {
    docker build -f (Join-Path $repo 'worker\Dockerfile') -t $Image $repo
    if ($LASTEXITCODE) { throw "docker build failed ($LASTEXITCODE)" }
}

$dockerArgs = @(
    'run', '--rm', '--cpus', $Cpus, '--memory', $Memory,
    '--mount', "type=bind,source=$WreckfestDir,target=/wreckfest,readonly"
)
if (-not $UseBakedScripts) {
    foreach ($dir in 'scripts', 'blender', 'smoke') {
        $dockerArgs += @('--mount', "type=bind,source=$(Join-Path $repo "worker\$dir"),target=/opt/worker/$dir,readonly")
    }
}
$dockerArgs += @($Image, 'smoke') + $Tests

Write-Host "docker $($dockerArgs -join ' ')"
& docker @dockerArgs
exit $LASTEXITCODE
