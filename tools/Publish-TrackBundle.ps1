<#
.SYNOPSIS
  Publish (or update) an installed track bundle to the Steam Workshop with Bugbear's uploader.

.DESCRIPTION
  Runs <game>\tools\workshop\Uploader.exe -uploadmod=<game>\mods\<slug>, the same call BagEdit's
  "Mod Publish" makes. It uploads as the Steam account logged in to the running Steam client.
  The uploader reads modinfo.modi (title, description, visibility, published id), uploads the content
  and preview, and writes the assigned published id back into modinfo.modi; this script then
  records that id in tools\bundles\<slug>.json so later bundle rebuilds update the same item.

  Publishing is public: without -Publish the script only checks and prints what it would do.
  Chris must approve every publish.

.EXAMPLE
  .\tools\Publish-TrackBundle.ps1 -Config tools\bundles\msvanglesey.json            # dry run
  .\tools\Publish-TrackBundle.ps1 -Config tools\bundles\msvanglesey.json -Publish   # upload
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Config,
    [string]$GameDir = 'D:\SteamLibrary\steamapps\common\Wreckfest',
    [switch]$Publish,
    [int]$TimeoutMinutes = 30
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $MyInvocation.MyCommand.Path -Parent) -Parent
$cfgPath = (Resolve-Path $Config).Path
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$modDir = Join-Path $GameDir "mods\$($cfg.slug)"
$modinfo = Join-Path $modDir 'modinfo.modi'
$uploader = Join-Path $GameDir 'tools\workshop\Uploader.exe'
$py = Join-Path $repo 'geom\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'py' }

function Read-ModInfo {
    $code = "import json,sys; sys.path.insert(0, r'$repo\tools'); import build_track_bundle as b; " +
            "d = b.modinfo_read(open(r'$modinfo','rb').read()); print(json.dumps(d, default=str))"
    (& $py -I -c $code) | ConvertFrom-Json
}

foreach ($p in $uploader, $modDir, $modinfo) { if (-not (Test-Path $p)) { throw "Missing: $p (install the bundle with Install-TrackMod.ps1 -Bundle first)" } }
if (-not (Get-Process steam -ErrorAction SilentlyContinue)) { throw 'Steam is not running: start Steam and log in first.' }
if (Get-Process Wreckfest*, Wreckfest_x64 -ErrorAction SilentlyContinue) { throw 'Wreckfest is running: close it first.' }

$info = Read-ModInfo
$idBefore = [uint64]$info.publishedId
if ($idBefore -ne [uint64]$cfg.publishedId) {
    throw "Published id mismatch: modinfo.modi has $idBefore, $Config has $($cfg.publishedId). Rebuild/reinstall the bundle from the config before publishing."
}
$action = if ($idBefore -eq 0) { 'CREATE a new Workshop item' } else { "UPDATE Workshop item $idBefore" }
Write-Host "Bundle : $($info.title)  ($modDir)"
Write-Host "Action : $action, visibility $($info.visibility) (0 public, 1 friends, 2 hidden)"
Write-Host "Note   : $($info.changeNote)"
if (-not $Publish) { Write-Host 'Dry run: add -Publish to upload.'; return }

Write-Host 'Uploading via the running Steam client...'
$proc = Start-Process -FilePath $uploader -ArgumentList "-uploadmod=$modDir" -WorkingDirectory $GameDir -PassThru
if (-not $proc.WaitForExit($TimeoutMinutes * 60 * 1000)) { $proc.Kill(); throw "Uploader timed out after $TimeoutMinutes min" }
Write-Host "Uploader exit code $($proc.ExitCode)"

$idAfter = [uint64](Read-ModInfo).publishedId
if ($idAfter -eq 0) { throw 'No published id was written to modinfo.modi: the upload did not complete (check the uploader window/log, and the Workshop legal agreement in Steam).' }
if ($idAfter -ne [uint64]$cfg.publishedId) {
    $raw = Get-Content $cfgPath -Raw
    $raw = $raw -replace '("publishedId"\s*:\s*)\d+', "`${1}$idAfter"
    [IO.File]::WriteAllText($cfgPath, $raw)
    Write-Host "Recorded published id $idAfter in $Config (commit it)."
}
Write-Host "Workshop page: https://steamcommunity.com/sharedfiles/filedetails/?id=$idAfter"
