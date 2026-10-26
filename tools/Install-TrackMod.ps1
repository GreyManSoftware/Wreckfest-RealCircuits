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

    The list entry is named from the mod's modinfo.modi. A name that looks like a file name (e.g. 'x_c.bmap', written
    by older pipeline builds) is replaced by the title-cased slug, with a warning. -DisplayName sets the name
    explicitly and also renames an entry that is already in the list, without changing its enabled state.

    -Bundle installs a bundle built by tools\Build-TrackBundle.ps1 (bundle.json at its root lists the member mods): it
    switches the member mods off in the mod list first (the same track in two enabled mods clashes), and the bundle's
    own Steam Workshop copy (bundle.json publishedId) when subscribed, then installs and enables the bundle.
    -Uninstall -Bundle switches the bundle off, removes it and switches the installed members and the Workshop copy
    back on.

    Works under Windows PowerShell 5.1 (powershell -File ...) and PowerShell 7 (pwsh -File ...).

.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip E:\WreckfestMods\msvanglesey.zip -Bundle -Force
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Uninstall -Slug msvanglesey -Bundle
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -Force -Server
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -Enable
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Enable -Slug my_oval -WhatIf
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Enable -Slug shortcut_reverse -DisplayName 'Shortcut Reverse'
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
    # Mod list display name: overrides modinfo.modi and renames an entry that is already listed.
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')][Parameter(ParameterSetName = 'Enable')]
    [string]$DisplayName,
    [string]$GameDir = 'D:\SteamLibrary\steamapps\common\Wreckfest',
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')][switch]$Force,
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')][switch]$Server,
    # A bundle (tools\Build-TrackBundle.ps1, bundle.json at its root): install disables its member mods first and
    # enables the bundle; -Uninstall -Bundle disables and removes it, then re-enables the installed members.
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')][Parameter(ParameterSetName = 'Uninstall')]
    [switch]$Bundle,
    [string]$ServerDir,
    # Default <repo>\.local\tmp and <repo>\.local\modlist-backups, set in the body: Windows PowerShell 5.1 leaves
    # $PSScriptRoot empty while it binds parameter defaults.
    [string]$TempRoot,
    # Mod list location: explicit file, or the single Steam user under -SteamUserDataDir (pick one with -SteamUserId).
    [string]$ModListPath,
    [string]$SteamUserId,
    [string]$SteamUserDataDir = 'C:\Program Files (x86)\Steam\userdata',
    [string]$ModListBackupDir,
    # Process names that count as "the game is running" (tests point this at a fake).
    [string[]]$GameProcessName = @('Wreckfest_x64', 'Wreckfest')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression.FileSystem

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$repoRoot = Split-Path $scriptDir -Parent
if (-not $TempRoot) { $TempRoot = Join-Path $repoRoot '.local\tmp' }
if (-not $ModListBackupDir) { $ModListBackupDir = Join-Path $repoRoot '.local\modlist-backups' }

$markerName = '.trackgen'
$slugPattern = '^[a-z0-9_]+$'
if (-not $ServerDir) { $ServerDir = $GameDir }
$modsDir = Join-Path $GameDir 'mods'
$modListHelper = Join-Path $scriptDir 'wf_modlist.py'

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

function Get-FriendlyName([string]$ModSlug) { (Get-Culture).TextInfo.ToTitleCase(($ModSlug -replace '_', ' ')) }

function Get-ModDisplayName([string]$ModDir, [string]$ModSlug) {
    if ($DisplayName) { return $DisplayName }
    $modi = Join-Path $ModDir 'modinfo.modi'
    if (Test-Path -LiteralPath $modi -PathType Leaf) {
        try {
            $name = (Invoke-ModListHelper @('modinfo-name', $modi)).name
            # Older pipeline builds wrote the last generated texture's file name (<slug>_color_terrain_c.bmap) here.
            if ($name -and $name.Trim() -match '^\S+\.(bmap|scne|tga|png|jpg|dds|evse|envi|weli|modi)$') {
                $friendly = Get-FriendlyName $ModSlug
                Write-Warning "modinfo.modi in $ModDir names the mod '$name', a file name (rebuild the zip). Using '$friendly'; pass -DisplayName to choose."
                return $friendly
            }
            if ($name -and $name.Trim()) { return $name }
        }
        catch { Write-Warning "Could not read the mod name from ${modi}: $($_.Exception.Message)" }
    }
    $ModSlug
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

# Not Get-FileHash: under Windows PowerShell 5.1 it honours -WhatIf and returns nothing.
function Get-FileSha([string]$Path) {
    $full = [IO.Path]::Combine((Get-Location -PSProvider FileSystem).ProviderPath, $Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    $fs = [IO.File]::OpenRead($full)
    try { -join ($sha.ComputeHash($fs) | ForEach-Object { $_.ToString('X2') }) }
    finally { $fs.Dispose(); $sha.Dispose() }
}

function Update-ModList([string]$TargetSlug, [bool]$On, [string]$EntryName, [bool]$Rename = $false, [string]$WorkshopId) {
    $path = Resolve-ModListPath
    $state = if ($On) { 'enabled' } else { 'disabled' }
    New-Item -ItemType Directory -Path $TempRoot -Force -WhatIf:$false | Out-Null
    $staged = Join-Path $TempRoot ('modlist-' + [guid]::NewGuid().ToString('N') + '.mods')
    try {
        $origHash = Get-FileSha $path
        $before = Invoke-ModListHelper @('info', $path)
        $helperArgs = if ($WorkshopId) { @('set', $path, '--workshop', $WorkshopId, '--state', $state, '--out', $staged) }
                      else { @('set', $path, '--slug', $TargetSlug, '--state', $state, '--out', $staged) }
        if ($EntryName) { $helperArgs += @('--name', $EntryName) }
        if ($Rename) { $helperArgs += '--rename' }
        $plan = Invoke-ModListHelper $helperArgs
        $where = if ($WorkshopId) { "Workshop $WorkshopId" } else { "mods/$TargetSlug" }
        $label = if ($plan.entry) { "'$($plan.entry.name)' ($where)" } else { $where }
        if ($plan.action -eq 'noop') { Write-Host "Mod list: $label is already $state - nothing to do ($path)."; return }
        if ($plan.action -eq 'absent') { Write-Host "Mod list: $label is not in the list - nothing to $(if ($On) { 'enable' } else { 'disable' }) ($path)."; return }
        $what = if ($plan.action -eq 'added') { "add $label as a new enabled entry (order $($plan.entry.order))" }
                elseif ($plan.action -eq 'renamed') { "rename '$($plan.renamedFrom)' to $label (stays $state)" }
                else { "set $label $state ($($plan.before) -> $($plan.entry.enabledRaw))" }
        if ($plan.action -ne 'renamed' -and $plan.PSObject.Properties['renamedFrom']) { $what += " and rename it from '$($plan.renamedFrom)'" }

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
                    if ($n.name -ne $plan.entry.name) { throw "entry $i is named '$($n.name)', expected '$($plan.entry.name)'" }
                    if ($plan.action -ne 'added') {
                        $o = $old[$i]
                        if ($n.info -ne $o.info -or $n.order -ne $o.order -or $n.path -ne $o.path) { throw "entry $i changed more than its state or name" }
                        if ($plan.action -eq 'renamed' -and $n.enabledRaw -ne $o.enabledRaw) { throw "entry $i changed state during a rename" }
                    }
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

function Read-BundleMembers([string]$Json, [string]$Source) {
    try { $b = $Json | ConvertFrom-Json } catch { throw "$Source is not valid JSON: $($_.Exception.Message)" }
    if (-not $b.PSObject.Properties['format'] -or $b.format -ne 'trackgen-bundle/1') { throw "$Source is not a track bundle manifest (format trackgen-bundle/1)." }
    $members = @($b.members | ForEach-Object { [pscustomobject]@{ Slug = [string]$_.slug; Name = [string]$_.name } })
    if ($members.Count -eq 0) { throw "$Source lists no member mods." }
    foreach ($m in $members) { if ($m.Slug -cnotmatch $slugPattern) { throw "${Source}: invalid member slug '$($m.Slug)'." } }
    $members
}

# The bundle's Steam Workshop item (bundle.json publishedId), or '' when it isn't published.
function Read-BundlePublishedId([string]$Json) {
    $b = $Json | ConvertFrom-Json
    if ($b.PSObject.Properties['publishedId'] -and [string]$b.publishedId -match '^[1-9]\d*$') { return [string]$b.publishedId }
    ''
}

function Assert-GameClosed {
    $running = @(Get-RunningGame)
    if ($running -and -not $WhatIfPreference) {
        throw "Wreckfest is running (PID $(($running | ForEach-Object Id) -join ', ')). Close the game first - it rewrites modlist.mods when it exits."
    }
}

if ($ListMods) { Show-ModList; return }

if ($PSCmdlet.ParameterSetName -in 'Enable', 'Disable') {
    if ($Slug -cnotmatch $slugPattern) { throw "Invalid slug '$Slug' (expected [a-z0-9_]+)." }
    if ($Enable) {
        $target = Join-Path $modsDir $Slug
        if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "Not installed: $target. Install it first (or pass -GameDir)." }
        Update-ModList $Slug $true (Get-ModDisplayName $target $Slug) ([bool]$DisplayName)
    }
    else { Update-ModList $Slug $false $null }
    return
}

if ($Uninstall) {
    if ($Slug -notmatch $slugPattern) { throw "Invalid slug '$Slug' (expected [a-z0-9_]+)." }
    $target = Join-Path $modsDir $Slug
    if (-not (Test-Path -LiteralPath $target)) { throw "Not installed: $target" }
    if (-not (Test-Marked $target)) { throw "Refusing to remove $target - it has no $markerName marker (not installed by this script)." }
    if ($Bundle) {
        $manifest = Join-Path $target 'bundle.json'
        if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw "$target has no bundle.json - not a bundle (drop -Bundle)." }
        $json = Get-Content -LiteralPath $manifest -Raw
        $members = Read-BundleMembers $json $manifest
        $publishedId = Read-BundlePublishedId $json
        Assert-GameClosed
        Update-ModList $Slug $false $null
        if ($PSCmdlet.ShouldProcess($target, 'Remove bundle mod')) {
            Remove-Item -LiteralPath $target -Recurse -Force
            Write-Host "Removed $target."
        }
        foreach ($m in $members) {
            $dir = Join-Path $modsDir $m.Slug
            if (Test-Path -LiteralPath $dir -PathType Container) { Update-ModList $m.Slug $true (Get-ModDisplayName $dir $m.Slug) }
            else { Write-Host "Member $($m.Slug) is not installed in $modsDir - not re-enabled." }
        }
        if ($publishedId) { Update-ModList '' $true $null $false $publishedId }  # the Workshop copy, when subscribed
        return
    }
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
        $members = @()
        $publishedId = ''
        if ($Bundle) {
            $entry = $archive.GetEntry("$modSlug/bundle.json")
            if (-not $entry) { throw "Zip folder '$modSlug' has no bundle.json - not a bundle (build it with tools\Build-TrackBundle.ps1, or drop -Bundle)." }
            $reader = New-Object IO.StreamReader($entry.Open())
            try { $json = $reader.ReadToEnd() } finally { $reader.Dispose() }
            $members = Read-BundleMembers $json "$modSlug/bundle.json"
            $publishedId = Read-BundlePublishedId $json
            if (@($members | Where-Object { $_.Slug -eq $modSlug }).Count) { throw "Bundle '$modSlug' lists itself as a member." }
        }
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

    # every event folder: one per track (a bundle has one per member)
    $events = @(Get-ChildItem -LiteralPath (Join-Path $staged 'data\property\event') -Recurse -Filter *.evse -File -ErrorAction SilentlyContinue |
        ForEach-Object { $_.BaseName } | Sort-Object)

    if ($Bundle) {
        $Enable = [switch]$true
        # The same track in two enabled mods clashes: switch the member mods off before the bundle goes on.
        Assert-GameClosed
        foreach ($m in $members) { Update-ModList $m.Slug $false $null }
        # ... and the bundle's own Workshop copy: the same tracks again, from an older build. With both on (2026-10-26)
        # the AI drove off the road on the 7 circuits whose world the two builds placed differently (build_scene
        # placement), consistent with the game taking a layout from one copy and its base scene from the other.
        if ($publishedId) { Update-ModList '' $false $null $false $publishedId }
    }
    $entryName = if ($Enable) { Get-ModDisplayName $staged $modSlug } else { $null }
    if ($PSCmdlet.ShouldProcess($target, 'Install track mod')) {
        if (-not (Test-Path -LiteralPath $modsDir)) { New-Item -ItemType Directory -Path $modsDir | Out-Null }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $staged -Destination $target
        Write-Host "Installed $modSlug -> $target"
        if ($Enable) { Write-Host 'Next: start Wreckfest (the mod is enabled in the mod list below).' }
        else { Write-Host 'Next: start Wreckfest, open the Mods menu, enable this mod and restart the game when asked (or use -Enable).' }
        if ($events) { Write-Host "Then pick the event(s): $($events -join ', ')" }
        else { Write-Host "Then pick the '$modSlug' event in Custom Event (no .evse found under data\property\event\$modSlug)." }
    }
    if ($Enable) { Update-ModList $modSlug $true $entryName ([bool]$DisplayName) }

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
        $circuits = @(if ($Bundle) { $members } else { [pscustomobject]@{ Slug = $modSlug; Name = (Get-FriendlyName $modSlug) } })
        foreach ($c in $circuits) { Write-Host ("+     '^{0}(_|$)' = '{1}'" -f $c.Slug, $c.Name) }
        $evCount = 86; $cirCount = 33
        $startScript = Join-Path $repoRoot 'Start-WreckfestServer.ps1'
        if (Test-Path -LiteralPath $startScript) {
            $m = Select-String -LiteralPath $startScript -Pattern '\$expected\s*=\s*@\{\s*Events\s*=\s*(\d+);\s*Circuits\s*=\s*(\d+)' | Select-Object -First 1
            if ($m) { $evCount = [int]$m.Matches[0].Groups[1].Value; $cirCount = [int]$m.Matches[0].Groups[2].Value }
        }
        Write-Host '   $expected:'
        Write-Host ("-   `$expected = @{{ Events = {0}; Circuits = {1} }}" -f $evCount, $cirCount)
        Write-Host ("+   `$expected = @{{ Events = {0}; Circuits = {1} }}" -f ($evCount + $ids.Count), ($cirCount + $circuits.Count))
        if ($Bundle) { Write-Host "   If the member mods are on the server too: take them out of mods= and their el_add= lines and circuitMap entries (same events)." }
        Write-Host ''
        Write-Host 'Ask before applying; server voting, laps, bots and existing tracks are untouched.'
    }
}
finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue -WhatIf:$false
}
