<#
.SYNOPSIS
    Installs a generated Wreckfest track mod zip into the client, and optionally prints the server staging changes.

.DESCRIPTION
    The zip must contain exactly one top-level folder <slug> ([a-z0-9_]+) with a data\ folder inside.
    It is extracted under E:\Source\wreckfest\.local\tmp, then moved to <GameDir>\mods\<slug>.
    A .trackgen marker is written into the installed folder; -Force and -Uninstall only ever touch
    folders carrying that marker (mods\example and hand-made mods are never touched).

    -Server only PRINTS the server_config.cfg and Start-WreckfestServer.ps1 changes. Nothing is applied.

    -Enable marks the mod as enabled in the client's mod list (Steam userdata ...\228380\local\wreckfest\modlist.mods),
    so there is no need to enable it in the Mods menu and restart. -Disable clears it again and -ListMods prints the
    list. Writes refuse while Wreckfest is running, back up the file first (next to it and in .local\modlist-backups),
    and restore the backup automatically if the written file does not verify. Needs Python 3.11+ (py or python);
    the format is described in tools\docs\modlist-format.md.

.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -Force -Server
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -Enable
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Enable -Slug my_oval -WhatIf
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -ListMods
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Uninstall -Slug my_oval
#>
[CmdletBinding(SupportsShouldProcess, DefaultParameterSetName = 'Zip')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Zip')][string]$Zip,
    [Parameter(Mandatory, ParameterSetName = 'Url')][string]$Url,
    [Parameter(Mandatory, ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')]
    [Parameter(Mandatory, ParameterSetName = 'Enable')][switch]$Enable,
    [Parameter(Mandatory, ParameterSetName = 'Disable')][switch]$Disable,
    [Parameter(Mandatory, ParameterSetName = 'ListMods')][switch]$ListMods,
    [Parameter(Mandatory, ParameterSetName = 'Uninstall')][Parameter(Mandatory, ParameterSetName = 'Enable')]
    [Parameter(Mandatory, ParameterSetName = 'Disable')][string]$Slug,
    [string]$GameDir = 'D:\SteamLibrary\steamapps\common\Wreckfest',
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')][switch]$Force,
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')][switch]$Server,
    [string]$ServerDir,
    [string]$TempRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) '.local\tmp'),
    # Mod list location: explicit file, or the single Steam user under -SteamUserDataDir (pick one with -SteamUserId).
    [string]$ModListPath,
    [string]$SteamUserId,
    [string]$SteamUserDataDir = 'C:\Program Files (x86)\Steam\userdata',
    [string]$ModListBackupDir = (Join-Path (Split-Path $PSScriptRoot -Parent) '.local\modlist-backups'),
    # Process names that count as "the game is running" (tests point this at a fake).
    [string[]]$GameProcessName = @('Wreckfest_x64', 'Wreckfest')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression.FileSystem

$markerName = '.trackgen'
$slugPattern = '^[a-z0-9_]+$'
if (-not $ServerDir) { $ServerDir = $GameDir }
$modsDir = Join-Path $GameDir 'mods'
$modListHelper = Join-Path $PSScriptRoot 'wf_modlist.py'

function Test-Marked([string]$Path) { Test-Path -LiteralPath (Join-Path $Path $markerName) -PathType Leaf }

function Invoke-ModListHelper([string[]]$Arguments) {
    $py = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' }
          elseif (Get-Command python -ErrorAction SilentlyContinue) { 'python' }
          else { throw 'Python 3.11+ is needed for the mod list (py or python on PATH).' }
    $raw = (& $py $modListHelper @Arguments | Out-String).Trim()
    $code = $LASTEXITCODE
    $result = $null
    try { $result = $raw | ConvertFrom-Json } catch { $result = $null }
    if ($code -ne 0 -or $null -eq $result) {
        $msg = if ($result -and $result.PSObject.Properties['error']) { $result.error } else { $raw }
        throw "wf_modlist.py $($Arguments[0]) failed: $msg"
    }
    $result
}

function Resolve-ModListPath {
    if ($ModListPath) {
        if (-not (Test-Path -LiteralPath $ModListPath -PathType Leaf)) { throw "Mod list not found: $ModListPath" }
        return (Resolve-Path -LiteralPath $ModListPath).Path
    }
    if ($SteamUserId) {
        if ($SteamUserId -notmatch '^\d+$') { throw "Invalid -SteamUserId '$SteamUserId' (expected the numeric userdata folder name)." }
        $p = Join-Path $SteamUserDataDir "$SteamUserId\228380\local\wreckfest\modlist.mods"
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "Mod list not found: $p" }
        return $p
    }
    $found = @(Get-ChildItem -Path (Join-Path $SteamUserDataDir '*\228380\local\wreckfest\modlist.mods') -File -ErrorAction SilentlyContinue)
    if ($found.Count -eq 0) { throw "No modlist.mods under $SteamUserDataDir\*\228380\local\wreckfest. Start Wreckfest once, or pass -ModListPath." }
    if ($found.Count -gt 1) {
        $ids = ($found | ForEach-Object { $_.FullName.Substring($SteamUserDataDir.TrimEnd('\').Length + 1).Split('\')[0] }) -join ', '
        throw "Found $($found.Count) Steam users with a Wreckfest mod list ($ids). Pass -SteamUserId <id>."
    }
    $found[0].FullName
}

function Get-RunningGame {
    foreach ($p in @(Get-Process -Name $GameProcessName -ErrorAction SilentlyContinue)) {
        $cmd = $null
        try { $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $($p.Id)" -ErrorAction Stop).CommandLine } catch { $cmd = $null }
        # A dedicated server (same exe, "-s server_config=...") uses <GameDir>\save\modlist.mods, not the client list.
        if ($cmd -and $cmd -match 'server_config') { continue }
        $p
    }
}

function Get-ModDisplayName([string]$ModDir, [string]$Fallback) {
    $modi = Join-Path $ModDir 'modinfo.modi'
    if (Test-Path -LiteralPath $modi -PathType Leaf) {
        try {
            $name = (Invoke-ModListHelper @('modinfo-name', $modi)).name
            if ($name -and $name.Trim()) { return $name }
        }
        catch { Write-Warning "Could not read the mod name from ${modi}: $($_.Exception.Message)" }
    }
    $Fallback
}

function Show-ModList {
    $path = Resolve-ModListPath
    $info = Invoke-ModListHelper @('info', $path)
    $entries = @($info.entries)
    Write-Output "Mod list: $path (bag format $($info.format), $($entries.Count) entries)"
    $entries | Format-Table -AutoSize -Property `
        @{ n = 'Order'; e = { if ($_.order -eq 2147483647) { '-' } else { $_.order } } },
        @{ n = 'Enabled'; e = { if ($_.enabled) { 'yes' } else { 'no' } } },
        @{ n = 'Name'; e = { $_.name } },
        @{ n = 'Path'; e = { $_.path } } | Out-String -Width 300 | Write-Output
    $running = @(Get-RunningGame)
    if ($running) { Write-Warning "Wreckfest is running (PID $(($running | ForEach-Object Id) -join ', ')); it rewrites this file when it exits." }
}

function Get-FileSha([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }

function Update-ModList([string]$TargetSlug, [bool]$On, [string]$DisplayName) {
    $path = Resolve-ModListPath
    $state = if ($On) { 'enabled' } else { 'disabled' }
    New-Item -ItemType Directory -Path $TempRoot -Force -WhatIf:$false | Out-Null
    $staged = Join-Path $TempRoot ('modlist-' + [guid]::NewGuid().ToString('N') + '.mods')
    try {
        $origHash = Get-FileSha $path
        $before = Invoke-ModListHelper @('info', $path)
        $helperArgs = @('set', $path, '--slug', $TargetSlug, '--state', $state, '--out', $staged)
        if ($DisplayName) { $helperArgs += @('--name', $DisplayName) }
        $plan = Invoke-ModListHelper $helperArgs
        $label = if ($plan.entry) { "'$($plan.entry.name)' (mods/$TargetSlug)" } else { "mods/$TargetSlug" }
        if ($plan.action -eq 'noop') { Write-Host "Mod list: $label is already $state - nothing to do ($path)."; return }
        if ($plan.action -eq 'absent') { Write-Host "Mod list: $label is not in the list - nothing to disable ($path)."; return }
        $what = if ($plan.action -eq 'added') { "add $label as a new enabled entry (order $($plan.entry.order))" }
                else { "set $label $state ($($plan.before) -> $($plan.entry.enabledRaw))" }

        $running = @(Get-RunningGame)
        if ($running) {
            $msg = "Wreckfest is running (PID $(($running | ForEach-Object Id) -join ', ')). Close the game first - it rewrites modlist.mods when it exits."
            if ($WhatIfPreference) { Write-Warning "$msg A real run would refuse." } else { throw $msg }
        }
        if (-not $PSCmdlet.ShouldProcess($path, "Mod list: $what")) { return }

        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $bakLocal = "$path.bak-$stamp"
        for ($i = 1; Test-Path -LiteralPath $bakLocal; $i++) { $bakLocal = "$path.bak-$stamp-$i" }
        New-Item -ItemType Directory -Path $ModListBackupDir -Force | Out-Null
        $bakRepo = Join-Path $ModListBackupDir (Split-Path $bakLocal -Leaf)
        Copy-Item -LiteralPath $path -Destination $bakLocal
        Copy-Item -LiteralPath $path -Destination $bakRepo -Force
        foreach ($b in $bakLocal, $bakRepo) {
            if ((Get-FileSha $b) -ne $origHash) { throw "Backup $b does not match $path; nothing was written." }
        }
        if ((Get-FileSha $path) -ne $origHash) { throw "$path changed while planning; nothing was written. Try again." }

        try {
            Copy-Item -LiteralPath $staged -Destination $path -Force
            if ($env:TRACKGEN_MODLIST_TEST_FAULT -eq 'corrupt-after-write') { [IO.File]::WriteAllBytes($path, [byte[]](7, 0, 0, 0)) }
            if ((Get-FileSha $path) -ne (Get-FileSha $staged)) { throw 'the written file differs from the staged file' }
            $after = Invoke-ModListHelper @('info', $path)
            $old = @($before.entries); $new = @($after.entries)
            $expectedCount = if ($plan.action -eq 'added') { $old.Count + 1 } else { $old.Count }
            if ($new.Count -ne $expectedCount) { throw "expected $expectedCount entries, found $($new.Count)" }
            for ($i = 0; $i -lt $new.Count; $i++) {
                $n = $new[$i]
                if ($i -eq $plan.entry.index) {
                    if ($n.path -ne $plan.entry.path -or [bool]$n.enabled -ne $On) { throw "entry $i is not $state after writing" }
                    continue
                }
                $o = $old[$i]
                if ($n.name -ne $o.name -or $n.path -ne $o.path -or $n.info -ne $o.info -or $n.enabledRaw -ne $o.enabledRaw -or $n.order -ne $o.order) {
                    throw "entry $i ('$($o.name)') changed unexpectedly"
                }
            }
        }
        catch {
            $reason = $_.Exception.Message
            Copy-Item -LiteralPath $bakLocal -Destination $path -Force
            if ((Get-FileSha $path) -ne $origHash) { throw "Mod list verification failed ($reason) and restoring $bakLocal also failed. Restore it by hand." }
            throw "Mod list verification failed ($reason). Restored the backup $bakLocal; the mod list is unchanged."
        }
        Write-Host "Mod list: $($plan.action) $label in $path"
        Write-Host "Backups: $bakLocal and $bakRepo"
    }
    finally {
        Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue -WhatIf:$false
    }
}

if ($ListMods) { Show-ModList; return }

if ($PSCmdlet.ParameterSetName -in 'Enable', 'Disable') {
    if ($Slug -cnotmatch $slugPattern) { throw "Invalid slug '$Slug' (expected [a-z0-9_]+)." }
    if ($Enable) {
        $target = Join-Path $modsDir $Slug
        if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "Not installed: $target. Install it first (or pass -GameDir)." }
        Update-ModList $Slug $true (Get-ModDisplayName $target $Slug)
    }
    else { Update-ModList $Slug $false $null }
    return
}

if ($Uninstall) {
    if ($Slug -notmatch $slugPattern) { throw "Invalid slug '$Slug' (expected [a-z0-9_]+)." }
    $target = Join-Path $modsDir $Slug
    if (-not (Test-Path -LiteralPath $target)) { throw "Not installed: $target" }
    if (-not (Test-Marked $target)) { throw "Refusing to remove $target - it has no $markerName marker (not installed by this script)." }
    if ($PSCmdlet.ShouldProcess($target, 'Remove track mod')) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "Removed $target. Disable it in the Mods menu (or with -Disable -Slug $Slug) if the game still lists it."
    }
    return
}

New-Item -ItemType Directory -Path $TempRoot -Force -WhatIf:$false | Out-Null
$work = Join-Path $TempRoot ("install-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work -WhatIf:$false | Out-Null

try {
    if ($Url) {
        $Zip = Join-Path $work 'download.zip'
        Write-Host "Downloading $Url"
        Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing -WhatIf:$false
    }
    if (-not (Test-Path -LiteralPath $Zip -PathType Leaf)) { throw "Zip not found: $Zip" }
    $Zip = (Resolve-Path -LiteralPath $Zip).Path

    # Validate every entry before extracting anything.
    $extractRoot = Join-Path $work 'x'
    $extractFull = [IO.Path]::GetFullPath($extractRoot).TrimEnd('\') + '\'
    $archive = [IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        $tops = @{}
        $hasData = $false
        foreach ($e in $archive.Entries) {
            $name = $e.FullName -replace '/', '\'
            if ($name -match '^[\\]' -or $name -match '^[A-Za-z]:' -or [IO.Path]::IsPathRooted($name)) {
                throw "Unsafe zip: absolute path entry '$($e.FullName)'."
            }
            $dest = [IO.Path]::GetFullPath((Join-Path $extractRoot $name))
            if (-not $dest.StartsWith($extractFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Unsafe zip: entry '$($e.FullName)' escapes the target folder."
            }
            $parts = $name.Split('\', [StringSplitOptions]::RemoveEmptyEntries)
            if ($parts.Count -eq 0) { continue }
            if ($parts.Count -eq 1 -and $e.Length -gt 0 -and -not $name.EndsWith('\')) {
                throw "Zip must contain a single top-level folder; found top-level file '$($e.FullName)'."
            }
            $tops[$parts[0]] = $true
            if ($parts.Count -ge 2 -and $parts[1] -eq 'data') { $hasData = $true }
        }
        if ($tops.Count -ne 1) { throw "Zip must contain exactly one top-level folder; found $($tops.Count): $($tops.Keys -join ', ')." }
        $modSlug = @($tops.Keys)[0]
        if ($modSlug -cnotmatch $slugPattern) { throw "Invalid mod folder name '$modSlug' (expected [a-z0-9_]+)." }
        if (-not $hasData) { throw "Zip folder '$modSlug' has no data\ folder." }
    }
    finally { $archive.Dispose() }

    $target = Join-Path $modsDir $modSlug
    if (Test-Path -LiteralPath $target) {
        if (-not (Test-Marked $target)) { throw "Refusing to overwrite $target - it was not installed by this script (no $markerName marker)." }
        if (-not $Force) { throw "$target already exists. Use -Force to replace it." }
    }

    [IO.Compression.ZipFile]::ExtractToDirectory($Zip, $extractRoot)
    $staged = Join-Path $extractRoot $modSlug
    Set-Content -LiteralPath (Join-Path $staged $markerName) -Value "Installed by tools/Install-TrackMod.ps1 on $(Get-Date -Format s) from $(Split-Path $Zip -Leaf)" -WhatIf:$false

    $events = @(Get-ChildItem -LiteralPath (Join-Path $staged "data\property\event\$modSlug") -Filter *.evse -File -ErrorAction SilentlyContinue |
        ForEach-Object { $_.BaseName } | Sort-Object)

    if ($PSCmdlet.ShouldProcess($target, 'Install track mod')) {
        if (-not (Test-Path -LiteralPath $modsDir)) { New-Item -ItemType Directory -Path $modsDir | Out-Null }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $staged -Destination $target
        Write-Host "Installed $modSlug -> $target"
        Write-Host 'Next: start Wreckfest, open the Mods menu, enable this mod and restart the game when asked.'
        if ($events) { Write-Host "Then pick the event(s): $($events -join ', ')" }
        else { Write-Host "Then pick the '$modSlug' event in Custom Event (no .evse found under data\property\event\$modSlug)." }
    }

    if ($Server) {
        $cfg = Join-Path $ServerDir 'server_config.cfg'
        $current = $null
        if (Test-Path -LiteralPath $cfg) {
            $current = Get-Content -LiteralPath $cfg | Where-Object { $_ -match '^\s*mods\s*=' } | Select-Object -Last 1
        }
        $mods = @()
        if ($current) { $mods = @(($current -replace '^\s*mods\s*=', '').Split(',') | ForEach-Object Trim | Where-Object { $_ }) }
        $newMods = 'mods=' + ((@($mods) + $modSlug | Select-Object -Unique) -join ',')
        $ids = if ($events) { $events } else { @($modSlug) }

        Write-Host ''
        Write-Host "=== Server staging for '$modSlug' (NOT applied) ==="
        Write-Host "1) Copy $target to $(Join-Path $ServerDir "mods\$modSlug") (same folder if the server uses this install)."
        Write-Host "2) $cfg"
        if ($current) { Write-Host "- $current" }
        Write-Host "+ $newMods"
        Write-Host '   In the AUTO-GENERATED EVENT ROTATION section, add:'
        foreach ($id in $ids) { Write-Host "+ el_add=$id" }
        Write-Host '3) Start-WreckfestServer.ps1'
        Write-Host '   $circuitMap:'
        $friendly = (Get-Culture).TextInfo.ToTitleCase(($modSlug -replace '_', ' '))
        Write-Host ("+     '^{0}(_|$)' = '{1}'" -f $modSlug, $friendly)
        $evCount = 86; $cirCount = 33
        $startScript = Join-Path (Split-Path $PSScriptRoot -Parent) 'Start-WreckfestServer.ps1'
        if (Test-Path -LiteralPath $startScript) {
            $m = Select-String -LiteralPath $startScript -Pattern '\$expected\s*=\s*@\{\s*Events\s*=\s*(\d+);\s*Circuits\s*=\s*(\d+)' | Select-Object -First 1
            if ($m) { $evCount = [int]$m.Matches[0].Groups[1].Value; $cirCount = [int]$m.Matches[0].Groups[2].Value }
        }
        Write-Host '   $expected:'
        Write-Host ("-   `$expected = @{{ Events = {0}; Circuits = {1} }}" -f $evCount, $cirCount)
        Write-Host ("+   `$expected = @{{ Events = {0}; Circuits = {1} }}" -f ($evCount + $ids.Count), ($cirCount + 1))
        Write-Host ''
        Write-Host 'Ask before applying; server voting, laps, bots and existing tracks are untouched.'
    }
}
finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue -WhatIf:$false
}
