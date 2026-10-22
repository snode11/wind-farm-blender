"""Open the installed MAPPO demo and keep the launcher's Bridge ready on demand."""
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
addon.load_demo_scene()
bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT')
print(f"[WFRL] MAPPO 60s loaded (OFFLINE RESULTS); Bridge ready at 127.0.0.1:{prefs.port}", flush=True)


_scene_requested = False
_recorded_session = None


def _sync_backend_scene():
    """After an explicit connection, record ownership and load the configured YAML."""
    global _scene_requested, _recorded_session
    state = addon.runtime.get_state()
    client = getattr(addon.runtime, "_client", None)
    session_id = getattr(client, "session_id", None)
    if not session_id or state.connection != 'CONNECTED' or not state.confirmed:
        _scene_requested = False
        return 0.25
    if session_id != _recorded_session:
        with open(os.environ["WFRL_SESSION_FILE"], "w", encoding="utf-8") as handle:
            handle.write(session_id)
        _recorded_session = session_id
    if state.error:
        return 0.25
    if not _scene_requested and not addon.runtime.workflow_scene and addon.runtime.configuration_editable():
        addon.runtime.send_workflow_command('scene.load', {'scene': prefs.default_scene})
        _scene_requested = True
        print(f'[WFRL] Bridge handshake confirmed; loading {prefs.default_scene}', flush=True)
    elif _scene_requested and addon.runtime.workflow_scene and addon.runtime.configuration_editable():
        from bl_ext.user_default.wfrl_blender import cameras
        cameras.select_camera(bpy.context.scene, 'WFRL.Camera.World')
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.show_region_ui = True
                    area.spaces.active.region_3d.view_perspective = 'CAMERA'
        print('[WFRL] CONNECTED / READY: configured scene loaded; no run started', flush=True)
        _scene_requested = False
    return 0.25


bpy.app.timers.register(_sync_backend_scene, first_interval=0.25)
