# Re-import the circuits whose recipes use measured widths (tools/real_tracks_data/measure_width.py) and print
# the width summary and the verify verdict of each. Run from the repo root with geom's venv:
#   pwsh -File tools\real_tracks_data\reimport_widths.ps1 [slug ...]
param([string[]]$Slugs = @('cadwellpark', 'brandshatch', 'oultonpark', 'snetterton', 'doningtonpark', 'bedfordautodrome', 'navarra'))
$ErrorActionPreference = 'Continue'  # the importer logs progress on stderr
$py = Join-Path $PSScriptRoot '..\..\geom\.venv\Scripts\python.exe'
$local = 'E:\Source\wreckfest\.local'
foreach ($s in $Slugs) {
    $cache = Join-Path $local "realtracks\$s"
    if (-not (Test-Path (Join-Path $cache 'osm.json'))) { $cache = Join-Path $local $s }
    $report = Join-Path $local "realtracks\$s\report-widths.json"
    & $py (Join-Path $PSScriptRoot '..\import_real_track.py') (Join-Path $PSScriptRoot "..\real_tracks\$s.json") `
        --out (Join-Path $PSScriptRoot "..\..\geom\tests\samples\$s.json") --report $report --cache $cache --verify `
        2> (Join-Path $local "realtracks\$s\import-widths.err") | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "$s FAILED (see import-widths.err)"; continue }
    $r = Get-Content $report -Raw | ConvertFrom-Json
    $w = $r.widths
    Write-Host ("{0}: width median {1} min {2} max {3}, line moved max {4} m; lap {5} m; valid {6} {7}" -f $s, $w.medianM, $w.minM, $w.maxM, $w.lineMovedMaxM, $r.verify.lapLengthM, $r.verify.valid, ($r.verify.violations -join '; '))
}
