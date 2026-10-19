<#
.SYNOPSIS
    Crash triage for generated tracks: game logs, crash dumps, enabled mods and scene-limit checks of the
    INSTALLED TrackGen mods. Read-only towards the game; writes only a report under .local\triage.

.EXAMPLE
    .\tools\Diagnose-TrackCrash.ps1                       # Error.log entries from the last 3 hours
    .\tools\Diagnose-TrackCrash.ps1 -Since '2026-10-19 20:30'

.NOTES
    Works in Windows PowerShell 5.1 and pwsh 7. Needs py (or python) with numpy for the clutter check.
    The checks are worker\builder\scne_read.py --limits (the same limits check_export.py applies to builds):
    largest draw batch, scene vertices, vertices with every prop instance, subscenes, unresolved subscenes and
    textures, clutter at full density. Crash dumps are listed, never copied.
#>
[CmdletBinding()]
param(
    [datetime] $Since = (Get-Date).AddHours(-3),
    [string] $GameDir = $(if ($env:WRECKFEST_DIR) { $env:WRECKFEST_DIR } else { 'D:\SteamLibrary\steamapps\common\Wreckfest' }),
    [string] $ModListPath,
    [string] $SteamUserDataDir = 'C:\Program Files (x86)\Steam\userdata',
    [string] $OutDir
)
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
if (-not $OutDir) { $OutDir = Join-Path $repoRoot '.local\triage' }
$py = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
$reader = Join-Path $repoRoot 'worker\builder\scne_read.py'
$lines = New-Object System.Collections.Generic.List[string]
function Out-Line([string]$s) { $lines.Add($s); Write-Host $s }

Out-Line "# Wreckfest crash triage, $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
Out-Line ''
$running = @(Get-Process -Name 'Wreckfest*' -ErrorAction SilentlyContinue)
Out-Line "Game running: $(if ($running.Count) { 'yes (PID ' + (($running | ForEach-Object Id) -join ', ') + ')' } else { 'no' })"

# ---- Error.log entries since -Since ("---- Log created on Mon Oct 19 20:37:14 2026 ----" blocks)
Out-Line ''
Out-Line "## Error.log since $($Since.ToString('yyyy-MM-dd HH:mm'))"
$errLog = Join-Path $GameDir 'Error.log'
if (Test-Path $errLog) {
    $block = $null; $keep = $false; $n = 0
    foreach ($l in Get-Content $errLog) {
        if ($l -match 'Log created on \w{3} (\w{3})\s+(\d+) (\d\d:\d\d:\d\d) (\d{4})') {
            $t = [datetime]::ParseExact("$($Matches[1]) $($Matches[2]) $($Matches[3]) $($Matches[4])", 'MMM d HH:mm:ss yyyy',
                [Globalization.CultureInfo]::InvariantCulture)
            $keep = $t -ge $Since
            if ($keep) { Out-Line "- $($t.ToString('HH:mm:ss'))"; $n++ }
        } elseif ($keep -and $l.Trim()) {
            Out-Line ('  ' + ($l -replace '^.*?\\src\\', 'src\'))
        }
    }
    if (-not $n) { Out-Line '(none)' }
} else { Out-Line '(no Error.log)' }

Out-Line ''
Out-Line '## log.txt (last 15 lines)'
$logTxt = Join-Path $GameDir 'log.txt'
if (Test-Path $logTxt) {
    Out-Line "($((Get-Item $logTxt).LastWriteTime.ToString('yyyy-MM-dd HH:mm')))"
    Get-Content $logTxt -Tail 15 | ForEach-Object { Out-Line "    $_" }
} else { Out-Line '(none)' }

Out-Line ''
Out-Line '## Newest crash files (metadata only)'
$crash = Join-Path $GameDir 'crash'
if (Test-Path $crash) {
    Get-ChildItem $crash -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $Since } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 8 |
        ForEach-Object { Out-Line ("- {0} {1,9:N0} B  {2}" -f $_.LastWriteTime.ToString('HH:mm:ss'), $_.Length, $_.FullName.Substring($crash.Length + 1)) }
}

# ---- enabled mods
Out-Line ''
Out-Line '## Enabled mods'
if (-not $ModListPath) {
    $found = @(Get-ChildItem -Path (Join-Path $SteamUserDataDir '*\228380\local\wreckfest\modlist.mods') -File -ErrorAction SilentlyContinue)
    if ($found.Count -eq 1) { $ModListPath = $found[0].FullName }
}
$enabled = @()
if ($ModListPath -and (Test-Path $ModListPath)) {
    $info = (& $py (Join-Path $scriptDir 'wf_modlist.py') info $ModListPath | Out-String) | ConvertFrom-Json
    $enabled = @($info.entries | Where-Object { $_.enabled })
    foreach ($e in $enabled) { Out-Line "- $($e.name.Trim()) ($($e.path))" }
} else { Out-Line '(mod list not found; pass -ModListPath)' }

# ---- checks of each enabled TrackGen mod (folder with the .trackgen marker)
Out-Line ''
Out-Line '## TrackGen mods: scene limits (installed files)'
$verdicts = @()
foreach ($e in $enabled) {
    if (-not $e.local) { continue }
    $dir = Join-Path $GameDir ($e.path.TrimEnd('/') -replace '/', '\')
    if (-not (Test-Path (Join-Path $dir '.trackgen'))) { continue }
    foreach ($base in Get-ChildItem (Join-Path $dir 'data\track') -Recurse -Filter '*_base.scne' -ErrorAction SilentlyContinue) {
        $out = & $py $reader --limits $base.FullName $dir $GameDir 2>&1 | ForEach-Object { "$_" }
        $ok = ($LASTEXITCODE -eq 0)
        $v = if ($ok) { 'OK' } else { 'OVER LIMIT / ERROR' }
        $verdicts += "$($e.name.Trim()): $v"
        Out-Line "### $($e.name.Trim()) ($($base.Name)): $v"
        $out | ForEach-Object { Out-Line "    $_" }
    }
}
if (-not $verdicts.Count) { Out-Line '(no enabled TrackGen mods)' }

Out-Line ''
Out-Line '## Verdict'
$verdicts | ForEach-Object { Out-Line "- $_" }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$report = Join-Path $OutDir ("{0}.md" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
[IO.File]::WriteAllLines($report, $lines)
Write-Host "Report: $report"
