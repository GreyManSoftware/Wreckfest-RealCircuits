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
        $s = $za.CreateEntry($k).Open()
        $v = $Entries[$k]
        if ($v -is [byte[]]) { $s.Write($v, 0, $v.Length); $s.Dispose(); continue }
        $w = New-Object IO.StreamWriter($s)
        $w.Write($v); $w.Dispose()
    }
    $za.Dispose(); $fs.Dispose()
    $path
}

$userData = Join-Path $root 'userdata'
$modListDir = Join-Path $userData '12345\228380\local\wreckfest'
$modList = Join-Path $modListDir 'modlist.mods'
$backups = Join-Path $root 'modlist-backups'

function Invoke-Install([hashtable]$Params) {
    $Params.GameDir = $game
    $Params.TempRoot = $tmp
    if (-not $Params.ContainsKey('ModListPath')) { $Params.SteamUserDataDir = $userData }
    $Params.ModListBackupDir = $backups
    if (-not $Params.ContainsKey('GameProcessName')) { $Params.GameProcessName = 'trackgen_no_such_game_process' }
    try { [pscustomobject]@{ Ok = $true; Out = (& $script @Params 6>&1 3>&1 | Out-String) } }
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

# --- Mod list (modlist.mods) — synthetic fixture under .local\fake-game, never the real Steam file ---
$helper = Join-Path $repo 'tools\wf_modlist.py'
$py = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
function Get-ModList([string]$Path) { (& $py $helper info $Path | Out-String | ConvertFrom-Json).entries }
function Get-Sha([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }
New-Item -ItemType Directory -Path $modListDir -Force | Out-Null
$fixtureScript = Join-Path $root 'make_fixture.py'
@"
import sys; sys.path.insert(0, r'$(Join-Path $repo 'tools')')
import wf_modlist as w
ws = 'd:/steamlibrary/steamapps/workshop/content/228380/'
e = [w.ModEntry('The Very TrackPack', b'idom', ws + '764432700/modinfo.modi', 1, 0, ws + '764432700'),
     w.ModEntry('Example Assets', b'idom', 'mods/example/modinfo.modi', 0, 1, 'mods/example/'),
     w.ModEntry(' WinkyIsland', b'idom', 'mods/winkyisland/modinfo.modi', 1, 2, 'mods/winkyisland/'),
     w.ModEntry('Speed Test Area', b'idom', ws + '2406354051/modinfo.modi', 0, 0x7FFFFFFF, ws + '2406354051')]
ml = w.ModList(w.Bag(7, b'sdom', 0, b''), e)
open(sys.argv[1], 'wb').write(w.modlist_encode(ml))
"@ | Set-Content -LiteralPath $fixtureScript -Encoding ascii
& $py $fixtureScript $modList
$fixtureHash = Get-Sha $modList
New-Item -ItemType Directory -Path "$game\mods\winkyisland" -Force | Out-Null
$rt = & $py $helper roundtrip $modList | ConvertFrom-Json
Check 'modlist fixture round-trips byte-identically' ($rt.byteIdentical -and $rt.decodeIdentical) ($rt | Out-String)
$realList = @(Get-ChildItem 'C:\Program Files (x86)\Steam\userdata\*\228380\local\wreckfest\modlist.mods' -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($realList) {
    $copy = Join-Path $root 'real-copy.mods'
    Copy-Item -LiteralPath $realList[0].FullName -Destination $copy
    $rt = & $py $helper roundtrip $copy | ConvertFrom-Json
    Check 'copy of the real modlist round-trips byte-identically' ($rt.byteIdentical -and $rt.decodeIdentical) ($rt | Out-String)
}

$r = Invoke-Install @{ ListMods = $true }
Check '-ListMods prints entries' ($r.Ok -and $r.Out -match 'Example Assets' -and $r.Out -match 'WinkyIsland' -and $r.Out -match 'yes' -and $r.Out -match 'no') $r.Out

$r = Invoke-Install @{ Enable = $true; Slug = 'winkyisland' }
Check 'enable already-enabled is a no-op' ($r.Ok -and $r.Out -match 'nothing to do' -and (Get-Sha $modList) -eq $fixtureHash -and -not (Test-Path $backups)) $r.Out

$r = Invoke-Install @{ Enable = $true; Slug = 'example'; WhatIf = $true }
Check 'enable -WhatIf writes nothing' ($r.Ok -and (Get-Sha $modList) -eq $fixtureHash -and -not (Test-Path $backups)) $r.Out

$r = Invoke-Install @{ Enable = $true; Slug = 'example'; GameProcessName = (Get-Process -Id $PID).ProcessName }
Check 'refuses while the game is running' (-not $r.Ok -and $r.Out -match 'running' -and (Get-Sha $modList) -eq $fixtureHash -and -not (Test-Path $backups)) $r.Out

$before = Get-ModList $modList
$r = Invoke-Install @{ Enable = $true; Slug = 'example' }
$after = Get-ModList $modList
$others = 0..3 | Where-Object { $_ -ne 1 } | Where-Object { ($before[$_] | ConvertTo-Json) -ne ($after[$_] | ConvertTo-Json) }
Check 'enable flips only the Example Assets flag' ($r.Ok -and $after[1].enabled -and -not $before[1].enabled -and @($others).Count -eq 0 -and @($after).Count -eq 4) $r.Out
$bakLocal = @(Get-ChildItem $modListDir -Filter 'modlist.mods.bak-*')
$bakRepo = @(Get-ChildItem $backups -Filter 'modlist.mods.bak-*' -ErrorAction SilentlyContinue)
Check 'backups created next to the list and in the backup dir' ($bakLocal.Count -eq 1 -and $bakRepo.Count -eq 1 -and (Get-Sha $bakLocal[0].FullName) -eq $fixtureHash -and (Get-Sha $bakRepo[0].FullName) -eq $fixtureHash)

Start-Sleep -Seconds 1
$r = Invoke-Install @{ Disable = $true; Slug = 'example' }
Check 'disable restores the original bytes' ($r.Ok -and (Get-Sha $modList) -eq $fixtureHash) $r.Out

$r = Invoke-Install @{ Disable = $true; Slug = 'nothere' }
Check 'disable of an unlisted mod is a no-op' ($r.Ok -and $r.Out -match 'not in the list' -and (Get-Sha $modList) -eq $fixtureHash) $r.Out

$modi = [byte[]]([byte[]](1, 0, 0, 0) + [Text.Encoding]::ASCII.GetBytes('idom') + [byte[]](0, 0, 0, 0, 7, 0, 0, 0) + [Text.Encoding]::ASCII.GetBytes('My Oval') + [byte[]](0, 0, 0, 0))
$withModi = New-TestZip 'withmodi' @{ 'my_oval/modinfo.modi' = $modi; 'my_oval/data/a.txt' = 'x' }
Start-Sleep -Seconds 1
$r = Invoke-Install @{ Zip = $withModi; Enable = $true; Force = $true }
$after = Get-ModList $modList
$same = @(0..3 | Where-Object { ($before[$_] | ConvertTo-Json) -eq ($after[$_] | ConvertTo-Json) }).Count
Check 'install -Enable adds a new entry, others intact' ($r.Ok -and @($after).Count -eq 5 -and $after[4].name -eq 'My Oval' -and $after[4].path -eq 'mods/my_oval/' -and $after[4].enabled -and $after[4].order -eq 3 -and $same -eq 4) $r.Out

$hashBefore = Get-Sha $modList
Start-Sleep -Seconds 1
$env:TRACKGEN_MODLIST_TEST_FAULT = 'corrupt-after-write'
try { $r = Invoke-Install @{ Disable = $true; Slug = 'my_oval' } } finally { Remove-Item Env:TRACKGEN_MODLIST_TEST_FAULT }
Check 'failed verification restores the backup' (-not $r.Ok -and $r.Out -match 'Restored the backup' -and (Get-Sha $modList) -eq $hashBefore) $r.Out

New-Item -ItemType Directory -Path (Join-Path $userData '67890\228380\local\wreckfest') -Force | Out-Null
Copy-Item $modList (Join-Path $userData '67890\228380\local\wreckfest\modlist.mods')
$r = Invoke-Install @{ ListMods = $true }
Check 'two Steam users require -SteamUserId' (-not $r.Ok -and $r.Out -match 'SteamUserId') $r.Out
$r = Invoke-Install @{ ListMods = $true; SteamUserId = '67890' }
Check '-SteamUserId picks that list' ($r.Ok -and $r.Out -match '67890' -and $r.Out -match 'My Oval') $r.Out
$r = Invoke-Install @{ ListMods = $true; ModListPath = $modList }
Check '-ModListPath works' ($r.Ok -and $r.Out -match 'My Oval') $r.Out
$leftLists = @(Get-ChildItem $tmp -Filter 'modlist-*.mods' -ErrorAction SilentlyContinue)
Check 'staged mod lists cleaned up' ($leftLists.Count -eq 0) ($leftLists -join ', ')

if ($script:fails) { Write-Host "$script:fails test(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'All tests passed' -ForegroundColor Green
