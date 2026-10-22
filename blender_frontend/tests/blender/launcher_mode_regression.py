"""Installed extension: MAPPO-first launcher, explicit backend connection and return.

Run against an idle Bridge with WFRL_PROJECT_DIR, WFRL_BACKEND_PYTHON,
WFRL_SCENE, WFRL_PORT and WFRL_SESSION_FILE set, as the launcher sets them.
This exercises real transport but does not start a solver or training run.
"""
from pathlib import Path
import os
import runpy
import time
import bpy

ROOT = Path(__file__).resolve().parents[3]
bootstrap = runpy.run_path(str(ROOT / 'scripts/blender/wfrl_blender_bootstrap.py'))
addon = bootstrap['addon']
runtime = addon.runtime
sync = bootstrap['_sync_backend_scene']
port = int(os.environ['WFRL_PORT'])
scene = bpy.context.scene
assert runtime.get_state().connection == 'OFFLINE RESULTS'
assert runtime._client is None
assert scene.frame_end == 3601 and len(addon.farm_flex._ACTIVE.blades) == 9
assert scene.camera.name == 'WFRL.Camera.T1.FrontQuarter'


def until(predicate):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        runtime.tick()
        sync()
        if runtime.get_state().error and runtime.progress() not in {'Connecting', 'Awaiting handshake'}:
            raise AssertionError(runtime.get_state().error)
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError(str(runtime.get_state()))


for mode in ('replay', 'interactive_training'):
    assert bpy.ops.wfrl.connection_mode(mode=mode) == {'FINISHED'}
    assert not addon.farm_flex.is_active(scene)
    assert bpy.ops.wfrl.bridge_connect() == {'FINISHED'}
    until(lambda: runtime.get_state().connection == 'CONNECTED'
          and bool(runtime.workflow_scene) and runtime.configuration_editable())
    assert len(runtime.workflow_scene['layout']) == 3
    assert runtime.desired_mode() == mode
    assert runtime.get_state().run_status == 'READY'
    assert scene.camera.name == 'WFRL.Camera.World'
    assert Path(os.environ['WFRL_SESSION_FILE']).read_text() == runtime.get_state().session_id
    assert bpy.ops.wfrl.connection_mode(mode='demo') == {'FINISHED'}
    assert runtime.get_state().connection == 'OFFLINE RESULTS'
    assert addon.farm_flex.is_active(scene) and scene.frame_end == 3601

bpy.app.timers.unregister(sync)
addon.unregister()
print('LAUNCHER_MAPPO_BACKEND_MODE_PASS', flush=True)
