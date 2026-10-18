#!/usr/bin/env bash
# Toolchain smoke tests, run inside the worker container.
#   run-smoke.sh            run all tests (a b c d)
#   run-smoke.sh b c        run only some
# Inputs are copied from the read-only game mount ($WRECKFEST_DIR) into $SMOKE_OUT; nothing is written to the mount.
# Exit code: number of failed tests.
set -uo pipefail

WF="${WRECKFEST_DIR:-/wreckfest}"
EXAMPLE="$WF/mods/example"
OUT="${SMOKE_OUT:-$HOME/smoke-out}"
SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_TIMEOUT="${SMOKE_TOOL_TIMEOUT:-600}"
BPY="$(ls /opt/blender/*/python/bin/python3.* 2>/dev/null | grep -E 'python3\.[0-9]+$' | head -n1)"

declare -A RESULT

rm -rf "$OUT" && mkdir -p "$OUT"

log() { printf '\n=== %s ===\n' "$*"; }

# size_of FILE -> bytes (0 if missing)
size_of() { [[ -f "$1" ]] && stat -c %s "$1" || echo 0; }

hexhead() { od -An -tx1 -N "${2:-12}" "$1" | tr -s ' ' | sed 's/^ //'; }

check_output() { # name file
    local n="$1" f="$2" s
    s=$(size_of "$f")
    if (( s > 0 )); then
        echo "PASS: $f ($s bytes, head: $(hexhead "$f" 8))"
        RESULT[$n]="PASS $(basename "$f") ${s}B"
    else
        echo "FAIL: $f missing or empty"
        RESULT[$n]="FAIL"
    fi
}

show_error_log() { # dir (not recursive: project roots contain links into the game)
    find "$1" -maxdepth 1 -iname 'error.log' -print0 2>/dev/null | while IFS= read -r -d '' f; do
        echo "--- $f"; head -n 40 "$f"
    done
}

test_a() {
    log "a: bimage.exe under Wine (.tga -> .bmap)"
    local p="$OUT/a" d="$OUT/a/data/art/textures/smoke"
    mkdir -p "$d"
    "$BPY" "$SMOKE_DIR/make_tga.py" "$d/smoke_grid_c5.tga" 256 || { RESULT[a]="FAIL (tga gen)"; return; }
    wf-project "$p"
    (cd "$d" && time timeout "$TOOL_TIMEOUT" wf-tool bimage -auto -input "$d/smoke_grid_c5.tga" -output "$d/smoke_grid_c5.bmap")
    echo "bimage exit code: $?"
    show_error_log "$d"
    check_output a "$d/smoke_grid_c5.bmap"
}

test_b() {
    log "b: bgeometry.exe under Wine (example .bgo3 -> .scne)"
    local rel="data/track/exampletrack"
    local name="${SMOKE_BGO:-example_race_track}"
    local p="$OUT/b" d="$OUT/b/$rel"
    mkdir -p "$d"
    cp "$EXAMPLE/$rel/$name.bgo3" "$d/" || { RESULT[b]="FAIL (no input)"; return; }
    wf-project "$p" "$EXAMPLE"
    rm -f "$d/$name.scne" # wf-project linked the stock output; build a fresh one
    (cd "$d" && time timeout "$TOOL_TIMEOUT" wf-tool bgeometry -v -input "$d/$name.bgo3" -output "$d/$name.scne")
    echo "bgeometry exit code: $?"
    show_error_log "$d"
    check_output b "$d/$name.scne"
    local stock="$EXAMPLE/$rel/$name.scne"
    if cmp -s "$stock" "$d/$name.scne"; then
        echo "identical to the stock $name.scne"
    else
        echo "differs from the stock $name.scne ($(size_of "$stock") bytes)"
    fi
}

test_c() {
    log "c: Toolbox Mz BGO exporter in blender -b, then bgeometry"
    local p="$OUT/c" d="$OUT/c/data/track/smoketrack"
    mkdir -p "$d"
    (cd "$d" && time timeout "$TOOL_TIMEOUT" blender -b --factory-startup --python-exit-code 1 \
        --python "$SMOKE_DIR/smoke_bgo_export.py" -- "$d/smoketrack.bgo3")
    local rc=$?
    echo "blender exit code: $rc"
    if (( rc != 0 )) || [[ ! -s "$d/smoketrack.bgo3" ]]; then
        echo "FAIL: exporter did not produce $d/smoketrack.bgo3"
        RESULT[c]="FAIL (export)"
        return
    fi
    echo "exported: $d/smoketrack.bgo3 ($(size_of "$d/smoketrack.bgo3") bytes, head: $(hexhead "$d/smoketrack.bgo3" 8))"
    wf-project "$p"
    (cd "$d" && time timeout "$TOOL_TIMEOUT" wf-tool bgeometry -v -input "$d/smoketrack.bgo3" -output "$d/smoketrack.scne")
    echo "bgeometry exit code: $?"
    show_error_log "$d"
    check_output c "$d/smoketrack.scne"
}

test_d() {
    log "d: Toolbox Mz bagwrite (.evse/.envi/.weli/.modi)"
    local d="$OUT/d"
    mkdir -p "$d"
    if "$BPY" "$SMOKE_DIR/smoke_bagwrite.py" "$d"; then
        RESULT[d]="PASS evse envi weli modi"
    else
        RESULT[d]="FAIL"
    fi
}

tests=("$@")
(( ${#tests[@]} )) || tests=(a b c d)
for t in "${tests[@]}"; do "test_$t"; done

log "summary"
fails=0
for t in "${tests[@]}"; do
    echo "$t: ${RESULT[$t]:-FAIL (not run)}"
    [[ "${RESULT[$t]:-FAIL}" == PASS* ]] || fails=$((fails + 1))
done
wineserver -k 2>/dev/null || true
exit "$fails"
