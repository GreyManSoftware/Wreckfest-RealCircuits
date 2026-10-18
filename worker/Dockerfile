# Placeholder: the worker image is built in the "toolchain-gate" todo. Do not build this file.
#
# Planned layout:
#   FROM debian:bookworm-slim (or ubuntu:24.04)
#   - install Wine64 (winehq stable), xvfb-free headless config, WINEPREFIX under /opt/wine
#   - download Blender 4.2 LTS (linux-x64 tarball, checksum-pinned) to /opt/blender
#   - install Toolbox Mz at a pinned commit into Blender's addons/scripts path
#   - pip install ../geom (trackgen_geom) into Blender's bundled Python
#   - VOLUME /gametools   <- Wreckfest tools + stock assets, mounted read-only at runtime, NEVER copied in
#   - non-root user, ENTRYPOINT runs the queue-driven build script
