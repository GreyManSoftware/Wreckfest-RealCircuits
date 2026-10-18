# Self-contained tests for Install-TrackMod.ps1. Runs ONLY against a fake game dir under .local\fake-game.
# Usage: powershell -File tools\tests\Install-TrackMod.Tests.ps1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem

$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$script = Join-Path $repo 'tools\Install-TrackMod.ps1'
$root = Join-Path $repo '.local\fake-game'
$game = Join-Path $root 'Wreckfest'
$zips = Join-Path $root 'zips'
$tmp = Join-Path $repo '.local\tmp'
if ($game -notlike '*\.local\fake-game\*') { throw 'Safety: test game dir must be under .local\fake-game' }

if (Test-Path $root) { Remove-Item $root -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $game 'mods\example\data'), $zips -Force | Out-Null
Set-Content (Join-Path $game 'mods\example\modinfo.modi') 'example'
$cfgText = "server_name=test`r`nmods=example`r`nel_add=loop`r`n"
Set-Content (Join-Path $game 'server_config.cfg') $cfgText -NoNewline
Set-Content (Join-Path $game 'initial_server_config.cfg') $cfgText -NoNewline
$cfgHashes = Get-FileHash (Join-Path $game '*.cfg')

function New-TestZip([string]$Name, [hashtable]$Entries) {
    $path = Join-Path $zips "$Name.zip"
    $fs = [IO.File]::Open($path, 'Create')
    $za = New-Object IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create)
    foreach ($k in $Entries.Keys) {
        $w = New-Object IO.StreamWriter($za.CreateEntry($k).Open())
        $w.Write($Entries[$k]); $w.Dispose()
    }
    $za.Dispose(); $fs.Dispose()
    $path
}

function Invoke-Install([hashtable]$Params) {
    $Params.GameDir = $game
    $Params.TempRoot = $tmp
    try { [pscustomobject]@{ Ok = $true; Out = (& $script @Params 6>&1 | Out-String) } }
    catch { [pscustomobject]@{ Ok = $false; Out = $_.Exception.Message } }
}

$script:fails = 0
function Check([string]$Name, [bool]$Cond, $Detail = '') {
    if ($Cond) { Write-Host "PASS $Name" -ForegroundColor Green }
    else { $script:fails++; Write-Host "FAIL $Name $Detail" -ForegroundColor Red }
}

$good = New-TestZip 'good' @{
    'my_oval/modinfo.modi' = 'x'
    'my_oval/preview.jpg' = 'x'
    'my_oval/data/property/event/my_oval/my_oval_race.evse' = 'x'
    'my_oval/data/property/event/my_oval/my_oval_derby.evse' = 'x'
}
$slip = New-TestZip 'slip' @{ 'my_oval/data/a.txt' = 'x'; 'my_oval/../../evil.txt' = 'x' }
$abs = New-TestZip 'abs' @{ 'my_oval/data/a.txt' = 'x'; 'C:/evil.txt' = 'x' }
$bad = New-TestZip 'badslug' @{ 'My-Oval/data/a.txt' = 'x' }
$nodata = New-TestZip 'nodata' @{ 'my_oval/modinfo.modi' = 'x' }
$example = New-TestZip 'example' @{ 'example/data/a.txt' = 'x' }

$target = Join-Path $game 'mods\my_oval'

$r = Invoke-Install @{ Zip = $good; WhatIf = $true }
Check 'WhatIf installs nothing' ($r.Ok -and -not (Test-Path $target)) $r.Out

$r = Invoke-Install @{ Zip = $good }
Check 'normal install' ($r.Ok -and (Test-Path "$target\data") -and (Test-Path "$target\.trackgen") -and (Test-Path "$target\modinfo.modi")) $r.Out
Check 'install prints next steps and events' ($r.Out -match 'Mods menu' -and $r.Out -match 'my_oval_race') $r.Out

$r = Invoke-Install @{ Zip = $good }
Check 'refuses without -Force' (-not $r.Ok -and $r.Out -match '-Force') $r.Out

Set-Content "$target\stale.txt" 'old'
$r = Invoke-Install @{ Zip = $good; Force = $true }
Check '-Force replaces marked folder' ($r.Ok -and -not (Test-Path "$target\stale.txt") -and (Test-Path "$target\.trackgen")) $r.Out

$r = Invoke-Install @{ Zip = $example; Force = $true }
Check '-Force refuses unmarked mods\example' (-not $r.Ok -and $r.Out -match 'marker' -and -not (Test-Path "$game\mods\example\.trackgen") -and (Test-Path "$game\mods\example\modinfo.modi")) $r.Out

$r = Invoke-Install @{ Zip = $slip; Force = $true }
Check 'zip-slip rejected' (-not $r.Ok -and $r.Out -match 'escapes' -and -not (Test-Path (Join-Path $root 'evil.txt')) -and -not (Test-Path (Join-Path $tmp 'evil.txt'))) $r.Out

$r = Invoke-Install @{ Zip = $abs; Force = $true }
Check 'absolute path rejected' (-not $r.Ok -and $r.Out -match 'absolute') $r.Out

$r = Invoke-Install @{ Zip = $bad }
Check 'bad slug rejected' (-not $r.Ok -and $r.Out -match 'Invalid mod folder') $r.Out

$r = Invoke-Install @{ Zip = $nodata }
Check 'missing data\ rejected' (-not $r.Ok -and $r.Out -match 'no data') $r.Out

$r = Invoke-Install @{ Zip = $good; Force = $true; Server = $true }
Check '-Server prints mods=' ($r.Ok -and $r.Out -match '\+ mods=example,my_oval') $r.Out
Check '-Server prints el_add=' ($r.Out -match '\+ el_add=my_oval_derby' -and $r.Out -match '\+ el_add=my_oval_race') $r.Out
Check '-Server prints circuitMap/expected' ($r.Out -match "'\^my_oval\(_\|\$\)' = 'My Oval'" -and $r.Out -match 'Circuits = 34') $r.Out
Check '-Server prints ask-before note' ($r.Out -match 'Ask before applying') $r.Out
$after = Get-FileHash (Join-Path $game '*.cfg')
Check 'cfg files unchanged' (($cfgHashes.Hash -join ',') -eq ($after.Hash -join ',') -and @($after).Count -eq 2)

$r = Invoke-Install @{ Uninstall = $true; Slug = 'example' }
Check 'uninstall refuses unmarked example' (-not $r.Ok -and (Test-Path "$game\mods\example")) $r.Out

$r = Invoke-Install @{ Uninstall = $true; Slug = 'my_oval'; WhatIf = $true }
Check 'uninstall WhatIf keeps folder' ($r.Ok -and (Test-Path $target)) $r.Out

$r = Invoke-Install @{ Uninstall = $true; Slug = 'my_oval' }
Check 'uninstall removes marked folder' ($r.Ok -and -not (Test-Path $target)) $r.Out

$left = @(Get-ChildItem $tmp -Directory -Filter 'install-*' -ErrorAction SilentlyContinue)
Check 'temp dirs cleaned up' ($left.Count -eq 0) ($left -join ', ')

if ($script:fails) { Write-Host "$script:fails test(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'All tests passed' -ForegroundColor Green
