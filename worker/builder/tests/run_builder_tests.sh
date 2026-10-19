#!/usr/bin/env bash
# Blender builder tests, run inside the worker container (see run-builder-tests.ps1).
#   run_builder_tests.sh                 all specs (the geom samples plus variants)
#   run_builder_tests.sh oval hilly_circuit
# For each spec: build the scenes (build_scene.py), check them (check_scene.py), export the base and every
# layout with Toolbox Mz, build each .bgo3 with bgeometry under Wine (base first: layouts reference it),
# then check the exported files (check_export.py). Finally a determinism check builds one spec twice and
# compares the exported .bgo3 bytes. Outputs go to $BUILDER_OUT (default /out). Exit code = failures.
set -uo pipefail

B="${BUILDER_DIR:-/opt/worker/builder}"
T="$B/tests"
SAMPLES="${BUILDER_SAMPLES:-/samples}"
OUT="${BUILDER_OUT:-/out}"
TOOL_TIMEOUT="${BUILDER_TOOL_TIMEOUT:-900}"
DETERMINISM="${BUILDER_DETERMINISM-oval}"
WORK="${BUILDER_WORK:-$HOME/builder-work}"  # bgeometry fails reading from Docker Desktop bind mounts
BPY="$(ls /opt/blender/*/python/bin/python3.* 2>/dev/null | grep -E 'python3\.[0-9]+$' | head -n1)"
BLENDER=(blender -b --factory-startup --python-exit-code 1)

declare -A RESULT
fails=0

log() { printf '\n=== %s ===\n' "$*"; }

mkdir -p "$OUT/specs"
"$BPY" "$T/make_variants.py" "$SAMPLES" "$OUT/specs" || exit 1

# scenes_of DIR -> scene names, base first
scenes_of() {
    "$BPY" -c 'import json,sys; m=json.load(open(sys.argv[1])); s=list(m["scenes"]); print(" ".join(sorted(s, key=lambda n: not n.endswith("_base"))))' "$1/manifest.json"
}
slug_of() { "$BPY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["slug"])' "$1/manifest.json"; }

# export_and_build DIR -> 0 when every scene exported and bgeometry built it without errors
export_and_build() {
    local dir="$1" slug scene track project rc=0 out
    slug="$(slug_of "$dir")"
    # The project root lives on the container's own filesystem: Wine's bgeometry fails with "Fatal disk
    # error ... ReadCacheBlock" on files in a Docker Desktop bind mount. Results are copied back below.
    project="$WORK/$(basename "$dir")/project"
    track="$project/data/track/$slug"
    rm -rf "$project" "$dir/project" && mkdir -p "$track"
    # Generated maps: TGA -> .bmap with bimage (as the pipeline does), from the container's filesystem
    local tex="$dir/mod/data/track/$slug/textures" stage="$WORK/$(basename "$dir")/mod"
    if [[ -d "$tex" ]]; then
        rm -rf "$stage" && mkdir -p "$stage/data/track/$slug" "$track/textures"
        cp -r "$tex" "$stage/data/track/$slug/"
        local nt=0 bad=0
        for t in "$stage/data/track/$slug/textures"/*.tga; do
            if (cd "$(dirname "$t")" && timeout "$TOOL_TIMEOUT" wf-tool bimage -auto -input "$t" \
                -output "$track/textures/$(basename "${t%.tga}").bmap" > /dev/null 2>&1) \
                && [[ -s "$track/textures/$(basename "${t%.tga}").bmap" ]]; then nt=$((nt + 1)); else bad=$((bad + 1)); fi
        done
        if (( bad == 0 && nt > 0 )); then echo "PASS: bimage $nt generated maps"; else echo "FAIL: bimage ($bad failed)"; rc=1; fi
    fi
    # Track data (.tcat, one per layout) beside the layout scenes, as the pipeline ships it (check_export)
    cp "$dir/mod/data/track/$slug/"*.tcat "$track/" 2>/dev/null || true
    for scene in $(scenes_of "$dir"); do
        if ! "${BLENDER[@]}" --python "$T/export_scene.py" -- "$dir/$scene.blend" "$track/$scene.bgo3" \
            > "$dir/export_$scene.log" 2>&1; then
            echo "FAIL: export $scene (see $dir/export_$scene.log)"; tail -n 20 "$dir/export_$scene.log"; rc=1; continue
        fi
        echo "PASS: export $scene ($(stat -c %s "$track/$scene.bgo3") bytes)"
    done
    wf-project "$project"
    for scene in $(scenes_of "$dir"); do
        [[ -s "$track/$scene.bgo3" ]] || continue
        rm -f "$track/Error.log" "$project/Error.log"
        out="$(cd "$track" && timeout "$TOOL_TIMEOUT" wf-tool bgeometry -v -input "$track/$scene.bgo3" -output "$track/$scene.scne" 2>&1)"
        local code=$?
        echo "$out" > "$dir/bgeometry_$scene.log"
        # Error.log: bgeometry logs "custom data has illegal key otherway/crossstart" although it applies the
        # key (the .scne differs with and without it), so those lines are known noise; anything else fails.
        local errlog="" errs=""
        for f in "$track/Error.log" "$project/Error.log"; do
            if [[ -s "$f" ]]; then errlog="$track/Error_$scene.log"; cat "$f" >> "$errlog"; rm -f "$f"; fi
        done
        [[ -n "$errlog" ]] && errs="$(grep -vE 'Log created|illegal key (otherway|crossstart)|^[[:space:]]*$' "$errlog")"
        # "Surface Settings does not exists: decal|noshadow" is a known notice: render tags that stock
        # materials use as well (#decal on road edges, #noshadow on the far ground)
        if (( code != 0 )) || [[ ! -s "$track/$scene.scne" ]] || grep -vE 'XDG_RUNTIME_DIR|Surface Settings does not exists?: (decal|noshadow) \(\)' <<<"$out" | grep -qiE '(^|[^a-z])(error|failed|warning|fatal)' || [[ -n "$errs" ]]; then
            echo "FAIL: bgeometry $scene (exit $code)"; echo "$out" | grep -v XDG_RUNTIME_DIR | tail -n 30
            [[ -n "$errlog" ]] && { echo "--- $errlog"; head -n 40 "$errlog"; }
            rc=1
        else
            echo "PASS: bgeometry $scene -> $(stat -c %s "$track/$scene.scne") bytes${errlog:+ (Error.log: known otherway/crossstart notice only)}"
        fi
    done
    mkdir -p "$dir/project/data/track/$slug"
    find "$track" -maxdepth 1 -type f -exec cp {} "$dir/project/data/track/$slug/" \;
    "$BPY" "$T/check_export.py" "$dir" "$project" || rc=1
    return $rc
}

run_spec() {
    local name="$1" spec="$OUT/specs/$1.json" dir="$OUT/$1" rc=0
    log "$name"
    rm -rf "$dir" && mkdir -p "$dir"
    if ! "${BLENDER[@]}" --python "$B/build_scene.py" -- --spec "$spec" --out "$dir" > "$dir/build.log" 2>&1; then
        echo "FAIL: build (see $dir/build.log)"; grep -v BKE_icon "$dir/build.log" | tail -n 30
        RESULT[$name]="FAIL (build)"; return 1
    fi
    grep -E '^(built|  warning)' "$dir/build.log"
    "${BLENDER[@]}" --python "$T/check_scene.py" -- "$dir" "$spec" 2>&1 | grep -E '^(PASS|FAIL|CHECKS|Traceback|  File|[A-Za-z]+Error)' \
        | tee "$dir/check.log"
    grep -q '^CHECKS OK' "$dir/check.log" || rc=1
    export_and_build "$dir" | tee "$dir/export.log"
    grep -q '^FAIL' "$dir/export.log" && rc=1
    grep -q '^EXPORT CHECKS OK' "$dir/export.log" || rc=1
    if (( rc == 0 )); then
        RESULT[$name]="PASS $(grep -c '^PASS' "$dir/check.log") scene checks, $(grep -c '^PASS' "$dir/export.log") export checks"
    else
        RESULT[$name]="FAIL ($(grep -c '^FAIL' "$dir/check.log" "$dir/export.log" | awk -F: '{s+=$2} END {print s}') failed checks)"
    fi
    return $rc
}

determinism() {
    local name="$1" spec="$OUT/specs/$1.json" a="$OUT/determinism/a" b="$OUT/determinism/b"
    log "determinism: $name built twice"
    rm -rf "$OUT/determinism"
    for d in "$a" "$b"; do
        mkdir -p "$d"
        "${BLENDER[@]}" --python "$B/build_scene.py" -- --spec "$spec" --out "$d" > "$d/build.log" 2>&1 || { RESULT[determinism]="FAIL (build)"; return 1; }
        local slug; slug="$(slug_of "$d")"
        for scene in $(scenes_of "$d"); do
            "${BLENDER[@]}" --python "$T/export_scene.py" -- "$d/$scene.blend" "$d/out/$scene.bgo3" > /dev/null 2>&1
        done
    done
    if [[ -n "$(ls "$a/out" 2>/dev/null)" ]] && diff <(cd "$a/out" && sha256sum *.bgo3) <(cd "$b/out" && sha256sum *.bgo3) \
        && cmp -s "$a/model.json" "$b/model.json" && cmp -s "$a/scenery.json" "$b/scenery.json" \
        && diff <(cd "$a/mod" && find . -type f -exec sha256sum {} + | sort) <(cd "$b/mod" && find . -type f -exec sha256sum {} + | sort) > /dev/null; then
        echo "PASS: identical model.json, scenery.json, generated maps and .bgo3 files"; (cd "$a/out" && sha256sum *.bgo3)
        RESULT[determinism]="PASS"
    else
        echo "FAIL: builds differ"; RESULT[determinism]="FAIL"; return 1
    fi
}

names=("$@")
if (( ${#names[@]} == 0 )); then
    for f in "$OUT"/specs/*.json; do names+=("$(basename "$f" .json)"); done
fi
for n in "${names[@]}"; do run_spec "$n" || fails=$((fails + 1)); done
if [[ -n "$DETERMINISM" ]]; then determinism "$DETERMINISM" || fails=$((fails + 1)); fi

log "summary"
for n in "${names[@]}" ${DETERMINISM:+determinism}; do echo "$n: ${RESULT[$n]:-FAIL (not run)}"; done
wineserver -k 2>/dev/null || true
exit "$fails"
