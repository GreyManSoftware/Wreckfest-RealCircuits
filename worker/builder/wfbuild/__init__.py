"""Blender scene builder for the Wreckfest Track Generator (see worker/README.md, "Blender builder").

Pure-numpy geometry lives in ``geometry``, ``layout`` and ``scatter``; ``bscene`` is the only module that
imports ``bpy``. The entry point is ``worker/builder/build_scene.py``.
"""

BUILDER_VERSION = "0.1.0"
