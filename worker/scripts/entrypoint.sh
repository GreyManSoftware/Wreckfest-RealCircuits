#!/usr/bin/env bash
# Worker entrypoint.
#   info          print tool versions and check the read-only game mount (default)
#   smoke [a..d]  run the toolchain smoke tests (see /opt/worker/smoke/run-smoke.sh)
#   <cmd> ...     run any command (e.g. blender -b ..., wf-tool bgeometry ...)
set -euo pipefail

export WRECKFEST_DIR="${WRECKFEST_DIR:-/wreckfest}"

# The game tools are console apps and don't need a display. Set WORKER_XVFB=1 if a tool ever does.
if [[ "${WORKER_XVFB:-0}" == "1" && -z "${DISPLAY:-}" ]]; then
    Xvfb :99 -screen 0 1024x768x24 -nolisten tcp >/dev/null 2>&1 &
    export DISPLAY=:99
fi

info() {
    echo "blender: $(blender --version 2>/dev/null | head -n1)"
    echo "wine:    $(wine --version 2>/dev/null)"
    echo "toolbox: $(cat "${BLENDER_SYSTEM_SCRIPTS}/addons/wreckfest_toolbox/PINNED_COMMIT")"
    for exe in bgeometry.exe bimage.exe; do
        if [[ -f "${WRECKFEST_DIR}/tools/${exe}" ]]; then
            echo "game:    ${WRECKFEST_DIR}/tools/${exe} found"
        else
            echo "game:    ${WRECKFEST_DIR}/tools/${exe} MISSING (mount the Wreckfest install read-only at ${WRECKFEST_DIR})"
        fi
    done
}

case "${1:-info}" in
    info) info ;;
    smoke) shift; exec /opt/worker/smoke/run-smoke.sh "$@" ;;
    *) exec "$@" ;;
esac
