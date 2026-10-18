# syntax=docker/dockerfile:1
#
# Track build worker: Blender 4.2 LTS (headless) + Wreckfest Toolbox Mz + Wine64.
#
# Build context is the repo root (see docker-compose.yml):
#   docker build -f worker/Dockerfile -t trackgen-worker:dev .
#
# The Wreckfest install (bgeometry.exe, bimage.exe, stock assets) is NEVER copied into this image.
# Mount it read-only at runtime at $WRECKFEST_DIR (default /wreckfest).
#
# CPU only: nothing here uses or needs a GPU (Blender runs with -b and never renders).

FROM debian:trixie-slim

ARG BLENDER_VERSION=4.2.23
ARG BLENDER_SHA256=bea0eb3146be13eae6225409a117b215184f41b7f79e799f97cb3abb8f6dc404
# Toolbox Mz v1.1.8 (MIT; bagwrite.py and the SCNE importer are CC0). Redistribution is allowed.
ARG TOOLBOX_REPO=gmazy/wreckfest_toolbox
ARG TOOLBOX_COMMIT=e03a57aa739c860a7721224bf417c454b8c755df

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Wine (Debian's wine 10.0, 64-bit only: the game tools are x64 console apps), Xvfb for the rare case a
# tool wants a display, and the X/GL client libraries Blender links against even in background mode.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils procps \
      wine wine64 \
      xvfb xauth \
      libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 \
      libgl1 libegl1 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# Blender LTS, checksum-pinned. Downloaded to a throwaway dir and deleted in the same layer.
RUN mkdir -p /opt/blender /var/cache/dl \
 && curl -fsSL --retry 3 -o /var/cache/dl/blender.tar.xz \
      "https://download.blender.org/release/Blender${BLENDER_VERSION%.*}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
 && echo "${BLENDER_SHA256}  /var/cache/dl/blender.tar.xz" | sha256sum -c - \
 && tar -xJf /var/cache/dl/blender.tar.xz -C /opt/blender --strip-components=1 \
 && rm -rf /var/cache/dl \
 && ln -s /opt/blender/blender /usr/local/bin/blender

# Toolbox Mz at a pinned commit, as a "system scripts" add-on so the module name is wreckfest_toolbox
# (the add-on's preferences bl_idname requires that exact folder name).
ENV BLENDER_SYSTEM_SCRIPTS=/opt/blender-scripts
RUN mkdir -p /opt/blender-scripts/addons/wreckfest_toolbox \
 && curl -fsSL --retry 3 "https://codeload.github.com/${TOOLBOX_REPO}/tar.gz/${TOOLBOX_COMMIT}" \
    | tar -xz -C /opt/blender-scripts/addons/wreckfest_toolbox --strip-components=1 \
 && echo "${TOOLBOX_REPO}@${TOOLBOX_COMMIT}" > /opt/blender-scripts/addons/wreckfest_toolbox/PINNED_COMMIT \
 && mkdir -p /usr/share/doc/wreckfest_toolbox \
 && cp /opt/blender-scripts/addons/wreckfest_toolbox/LICENSE /usr/share/doc/wreckfest_toolbox/LICENSE \
 && cp /opt/blender-scripts/addons/wreckfest_toolbox/addons/io_import_wreckfest/LICENSE /usr/share/doc/wreckfest_toolbox/LICENSE.CC0

# Non-root user with a pre-initialised 64-bit Wine prefix.
RUN useradd --create-home --uid 1000 --shell /bin/bash worker
USER worker
WORKDIR /home/worker
ENV HOME=/home/worker \
    WINEPREFIX=/home/worker/.wine \
    WINEARCH=win64 \
    WINEDEBUG=-all \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    WRECKFEST_DIR=/wreckfest
RUN wineboot --init && wineserver --wait

COPY --chmod=0755 worker/scripts/ /opt/worker/scripts/
COPY --chmod=0755 worker/smoke/ /opt/worker/smoke/
COPY worker/blender/ /opt/worker/blender/
ENV PATH=/opt/worker/scripts:$PATH

ENTRYPOINT ["/opt/worker/scripts/entrypoint.sh"]
CMD ["info"]
