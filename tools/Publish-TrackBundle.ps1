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

  The uploader always uploads the content too, so every publish is a new version that subscribers
  download (a small one when the files are unchanged). For a text-only change use -DescriptionOnly:
  it writes the installed bundle's title and description to .local\bundles\<slug>-description.txt,
  copies the description to the clipboard and opens the Workshop page, to paste in via "Edit title &
  description" there; nothing is uploaded and subscribers download nothing.

.EXAMPLE
  .\tools\Publish-TrackBundle.ps1 -Config tools\bundles\msvanglesey.json                    # dry run
  .\tools\Publish-TrackBundle.ps1 -Config tools\bundles\msvanglesey.json -Publish           # upload
  .\tools\Publish-TrackBundle.ps1 -Config tools\bundles\msvanglesey.json -DescriptionOnly   # text only, no upload
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Config,
    [string]$GameDir = 'D:\SteamLibrary\steamapps\common\Wreckfest',
    [switch]$Publish,
    [switch]$DescriptionOnly,
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

if ($Publish -and $DescriptionOnly) { throw '-Publish and -DescriptionOnly are exclusive: -DescriptionOnly never uploads.' }
foreach ($p in $uploader, $modDir, $modinfo) { if (-not (Test-Path $p)) { throw "Missing: $p (install the bundle with Install-TrackMod.ps1 -Bundle first)" } }

if ($DescriptionOnly) {
    $info = Read-ModInfo
    $id = [uint64]$info.publishedId
    if ($id -eq 0) { throw 'Not published yet: there is no Workshop page to edit (publish once with -Publish).' }
    $outDir = Join-Path $repo '.local\bundles'
    New-Item -ItemType Directory -Force $outDir | Out-Null
    $out = Join-Path $outDir "$($cfg.slug)-description.txt"
    [IO.File]::WriteAllText($out, "TITLE:`r`n$($info.title)`r`n`r`nDESCRIPTION:`r`n$($info.description)")
    Set-Clipboard -Value $info.description
    Write-Host "Title       : $($info.title)"
    Write-Host "Description : copied to the clipboard; title and description also in $out"
    Write-Host 'Paste it on the Workshop page (signed in as the owner: "Edit title & description"). Nothing is uploaded.'
    Start-Process "https://steamcommunity.com/sharedfiles/filedetails/?id=$id"
    return
}
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
