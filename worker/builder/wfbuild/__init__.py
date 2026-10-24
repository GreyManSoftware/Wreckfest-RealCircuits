"""Blender scene builder for the Wreckfest Track Generator (see worker/README.md, "Blender builder").

Pure-numpy modules: ``geometry``, ``layout``, ``fields``, ``noise``, ``maps``, ``backdrop``, ``materials``, ``envs``,
``dressing`` (the scenery planner, also usable without Blender), ``cameras`` and ``tcat`` (track data); ``bscene`` is the only module that imports
``bpy``. The entry point is ``worker/builder/build_scene.py``.
"""

BUILDER_VERSION = "0.2.0"
