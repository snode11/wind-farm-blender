"""Part 3 integration: registration, reload, timers and offline fallback."""
from pathlib import Path
import importlib
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'blender_frontend'))
import bpy
import wfrl_blender


def main():
    # Fails before Part3 implementation: no status panel/runtime exists.
    wfrl_blender.register()
    assert hasattr(bpy.types, 'WFRL_PT_connection'), 'Part 3 connection panel missing'
    from wfrl_blender import runtime
    assert bpy.app.timers.is_registered(runtime.tick)
    for _ in range(3):
        wfrl_blender.register()
        assert sum(h.__name__ == '_on_load' and h.__module__ == 'wfrl_blender'
                   for h in bpy.app.handlers.load_post) == 1
        old_tick = runtime.tick
        wfrl_blender.unregister()
        assert not bpy.app.timers.is_registered(old_tick)
        wfrl_blender.register()
    from wfrl_blender import presentation
    previous_handle = presentation._HANDLE
    importlib.reload(presentation)
    wfrl_blender.register()
    assert getattr(bpy, '_wfrl_overlay_handle', None) == presentation._HANDLE
    assert presentation._HANDLE is None  # Presentation overlays were removed.
    old_tick = runtime.tick
    importlib.reload(wfrl_blender)
    wfrl_blender.register()
    assert bpy.app.timers.is_registered(runtime.tick)
    # No configured backend dependencies: local controls remain usable.
    wfrl_blender.load_demo_scene()
    assert runtime.get_state().connection == 'LOCAL DEMO'
    assert bpy.ops.wfrl.demo_start.poll()
    bpy.ops.wfrl.demo_start()
    assert not bpy.ops.wfrl.demo_start.poll(), 'Duplicate start must be disabled'
    bpy.ops.wfrl.demo_pause()
    assert not bpy.ops.wfrl.load_demo.poll(), 'Active paused scene must be protected'
    bpy.ops.wfrl.demo_step()
    bpy.ops.wfrl.demo_resume()
    bpy.ops.wfrl.demo_stop()
    assert bpy.context.scene.get('wfrl_run_status') == 'STOPPED'
    assert bpy.ops.wfrl.load_demo.poll()
    assert not ({'torch', 'mpi4py', 'floris', 'wfrl'} & sys.modules.keys())
    wfrl_blender.unregister()
    wfrl_blender.unregister()
    assert not bpy.app.timers.is_registered(runtime.tick)
    assert not hasattr(bpy.types, 'WFRL_PT_connection')
    assert not hasattr(bpy.types.Scene, 'wfrl_manual_yaw')
    print('WFRL_PART3_LIFECYCLE_SMOKE=PASS')

if __name__ == '__main__': main()
