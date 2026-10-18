<#
.SYNOPSIS
    Installs a generated Wreckfest track mod zip into the client, and optionally prints the server staging changes.

.DESCRIPTION
    The zip must contain exactly one top-level folder <slug> ([a-z0-9_]+) with a data\ folder inside.
    It is extracted under E:\Source\wreckfest\.local\tmp, then moved to <GameDir>\mods\<slug>.
    A .trackgen marker is written into the installed folder; -Force and -Uninstall only ever touch
    folders carrying that marker (mods\example and hand-made mods are never touched).

    -Server only PRINTS the server_config.cfg and Start-WreckfestServer.ps1 changes. Nothing is applied.

.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -Force -Server
.EXAMPLE
    .\tools\Install-TrackMod.ps1 -Uninstall -Slug my_oval
#>
[CmdletBinding(SupportsShouldProcess, DefaultParameterSetName = 'Zip')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Zip')][string]$Zip,
    [Parameter(Mandatory, ParameterSetName = 'Url')][string]$Url,
    [Parameter(Mandatory, ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [Parameter(Mandatory, ParameterSetName = 'Uninstall')][string]$Slug,
    [string]$GameDir = 'D:\SteamLibrary\steamapps\common\Wreckfest',
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')][switch]$Force,
    [Parameter(ParameterSetName = 'Zip')][Parameter(ParameterSetName = 'Url')][switch]$Server,
    [string]$ServerDir,
    [string]$TempRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) '.local\tmp')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression.FileSystem

$markerName = '.trackgen'
$slugPattern = '^[a-z0-9_]+$'
if (-not $ServerDir) { $ServerDir = $GameDir }
$modsDir = Join-Path $GameDir 'mods'

function Test-Marked([string]$Path) { Test-Path -LiteralPath (Join-Path $Path $markerName) -PathType Leaf }

if ($Uninstall) {
    if ($Slug -notmatch $slugPattern) { throw "Invalid slug '$Slug' (expected [a-z0-9_]+)." }
    $target = Join-Path $modsDir $Slug
    if (-not (Test-Path -LiteralPath $target)) { throw "Not installed: $target" }
    if (-not (Test-Marked $target)) { throw "Refusing to remove $target - it has no $markerName marker (not installed by this script)." }
    if ($PSCmdlet.ShouldProcess($target, 'Remove track mod')) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "Removed $target. Disable it in the Mods menu if the game still lists it."
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
