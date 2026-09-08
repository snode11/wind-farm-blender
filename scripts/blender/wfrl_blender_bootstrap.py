"""Executed by Blender: enable WFRL, apply launcher configuration, then connect."""
import importlib
import os

import bpy

MODULE = "bl_ext.user_default.wfrl_blender"
try:
    addon = importlib.import_module(MODULE)
except ImportError as exc:
    raise RuntimeError(
        "WFRL Blender extension is not installed. Build it with scripts/blender/build_extension.py "
        "and install the resulting dist/wfrl_blender-*.zip from Blender Preferences."
    ) from exc

if MODULE not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module=MODULE)
prefs = bpy.context.preferences.addons[MODULE].preferences
prefs.project_dir = os.environ["WFRL_PROJECT_DIR"]
prefs.backend_python = os.environ["WFRL_BACKEND_PYTHON"]
prefs.default_scene = os.environ["WFRL_SCENE"]
prefs.port = int(os.environ["WFRL_PORT"])
prefs.mpi_path = os.environ.get("WFRL_MPI", "")
prefs.fastfarm_path = os.environ.get("WFRL_FASTFARM", "")
addon.runtime.connect(prefs.port)
print(f"[WFRL] Extension enabled and connecting to 127.0.0.1:{prefs.port}", flush=True)


def _record_session():
    client = getattr(addon.runtime, "_client", None)
    session_id = getattr(client, "session_id", None)
    if session_id:
        with open(os.environ["WFRL_SESSION_FILE"], "w", encoding="utf-8") as handle:
            handle.write(session_id)
        return None
    return 0.1


bpy.app.timers.register(_record_session, first_interval=0.1)
