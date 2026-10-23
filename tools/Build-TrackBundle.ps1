<#
.SYNOPSIS
    Merges per-circuit track mod zips into one Workshop bundle mod (<slug>.zip) with its own modinfo.modi,
    preview.jpg, CREDITS.txt and bundle.json.

.DESCRIPTION
    Wraps tools\build_track_bundle.py (Python 3.11+ with numpy and Pillow). Re-runnable: each run rebuilds the
    bundle from the current member zips and replaces <OutDir>\<slug>.zip only when every check passed.

    The bundle keeps every member's data\ tree intact. The run fails, and writes no zip, on a file path two
    members share, a duplicate track/environment/event/layout/weather id, a file that would replace a stock
    game file, an unresolved event/weather reference, any scene-limit (scne_read --limits), driving-order or
    track-data (.tcat) check from worker\builder\tests\check_export.py on any layout, personal data in the
    shipped text, or a Workshop limit (title 128 bytes, description 8,000, preview under 1 MB).

    The bundle's settings (title, Published Id, visibility, member list, description parts, credits) live in a
    config file, by default tools\bundles\msvanglesey.json. After the first Workshop publish, put the assigned
    Published Id there so later builds keep it (docs\workshop-publishing.md).

    Works under Windows PowerShell 5.1 (powershell -File ...) and PowerShell 7 (pwsh -File ...).

.EXAMPLE
    .\tools\Build-TrackBundle.ps1                                   # tools\bundles\msvanglesey.json, zips in E:\WreckfestMods
.EXAMPLE
    .\tools\Build-TrackBundle.ps1 -KeepFolder                       # also leave E:\WreckfestMods\msvanglesey\
.EXAMPLE
    .\tools\Build-TrackBundle.ps1 -Slug test_pack -Title 'Test Pack' -Zip E:\WreckfestMods\a.zip, E:\WreckfestMods\b.zip -OutDir E:\Source\wreckfest\.local\bundles
#>
[CmdletBinding()]
param(
    [string]$Config,
    [string]$Slug,
    [string]$Title,
    [string[]]$Zip,
    [string]$ZipDir = 'E:\WreckfestMods',
    [string]$OutDir,
    [string]$GameDir = 'D:\SteamLibrary\steamapps\common\Wreckfest',
    [switch]$KeepFolder,
    # Python with numpy and Pillow (default: py, then python)
    [string]$Python
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$repoRoot = Split-Path $scriptDir -Parent
if (-not $Config -and -not $Zip) { $Config = Join-Path $scriptDir 'bundles\msvanglesey.json' }
if (-not $OutDir) { $OutDir = $ZipDir }

if (-not $Python) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $Python = 'py' }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = 'python' }
    else { throw 'Python 3.11+ with numpy and Pillow is needed (py or python on PATH, or -Python).' }
}

$name = if ($Slug) { $Slug } elseif ($Config) { [IO.Path]::GetFileNameWithoutExtension($Config) } else { 'bundle' }
$logDir = Join-Path $repoRoot '.local\bundles'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir "$name-checks.log"

$pyArgs = @((Join-Path $scriptDir 'build_track_bundle.py'), '--zip-dir', $ZipDir, '--out-dir', $OutDir, '--game-dir', $GameDir, '--log', $log)
if ($Config) { $pyArgs += @('--config', $Config) }
if ($Slug) { $pyArgs += @('--slug', $Slug) }
if ($Title) { $pyArgs += @('--title', $Title) }
foreach ($z in @($Zip | Where-Object { $_ })) { $pyArgs += @('--zip', $z) }
if ($KeepFolder) { $pyArgs += '--keep-folder' }

Write-Host "Building bundle ($name) from $(if ($Zip) { "$(@($Zip).Count) zips" } else { "the config's tracks in $ZipDir" }); checks log: $log"
$raw = (& $Python @pyArgs | Out-String).Trim()
$code = $LASTEXITCODE
$result = $null
try { $result = $raw | ConvertFrom-Json } catch { $result = $null }
if ($null -eq $result) { throw "build_track_bundle.py failed (exit $code): $raw" }
if ($result.PSObject.Properties['error']) { throw "build_track_bundle.py: $($result.error)" }

if (@($result.failures).Count -gt 0) {
    foreach ($f in @($result.failures)) { Write-Warning "FAIL: $f" }
    throw "Bundle checks failed ($(@($result.failures).Count); $($result.checksPassed) passed); no zip written. Details: $log"
}
foreach ($c in @($result.circuits)) { Write-Host ("  {0}: {1}" -f $c.name, (@($c.layouts) -join ', ')) }
Write-Host ("{0} circuits, {1} layouts, {2} files, {3:N1} MB unpacked; {4} checks passed" -f $result.members, $result.layouts, $result.files, ($result.unpackedBytes / 1MB), $result.checksPassed)
Write-Host ("Wrote {0} ({1:N1} MB, SHA-256 {2})" -f $result.zip, ($result.zipBytes / 1MB), $result.zipSha256)
Write-Host "Preview: $($result.preview)"
if ($result.PSObject.Properties['folder']) { Write-Host "Folder: $($result.folder)" }
Write-Host ("modinfo.modi: title '{0}', Published Id {1}, visibility {2} (0 = public), tags {3}" -f $result.modinfo.title, $result.modinfo.publishedId, $result.modinfo.visibility, $result.modinfo.tagBits)
