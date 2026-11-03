"""Native registration, reload, partial acquisition rollback and offline results."""
from pathlib import Path
import importlib
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'blender_frontend'))
import bpy
import wfrl_blender


def main():
    wfrl_blender.register()
    assert hasattr(bpy.types, 'WFRL_PT_connection'), 'Connection panel missing'
    from wfrl_blender import runtime
    from wfrl_blender import blade_recon_review, split_reconstruction_ui
    assert not bpy.app.timers.is_registered(blade_recon_review._tick)
    assert blade_recon_review._loaded not in bpy.app.handlers.load_post
    assert blade_recon_review._frame not in bpy.app.handlers.frame_change_post
    assert blade_recon_review.HUD_HANDLE is None
    identifiers = {cls.bl_idname for cls in wfrl_blender._classes() if hasattr(cls, 'bl_idname')}
    assert 'wfrl.blade_recon_load' not in identifiers
    assert bpy.ops.wfrl.split_recon_enter.get_rna_type().properties['mode'].default == 'CURRENT'
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
    assert runtime.get_state().connection == 'OFFLINE RESULTS'
    from wfrl_blender import farm_flex, clearance_replay
    scene=bpy.context.scene
    assert farm_flex.is_active(scene) and bpy.ops.wfrl.farm_transport.poll()
    stamp=clearance_replay.sample(scene)['time_s']
    bpy.ops.wfrl.farm_transport(action='STEP')
    assert clearance_replay.sample(scene)['time_s']>stamp
    bpy.ops.wfrl.farm_transport(action='RESET')
    assert scene.frame_current==scene.frame_start
    assert not ({'torch','mpi4py','floris'} & sys.modules.keys())
    wfrl_blender.unregister()
    wfrl_blender.unregister()
    assert not bpy.app.timers.is_registered(runtime.tick)
    assert not hasattr(bpy.types, 'WFRL_PT_connection')
    assert not hasattr(bpy.types.Scene, 'wfrl_manual_yaw')
    from unittest.mock import patch
    from wfrl_blender.panels import home
    from wfrl_blender.radar_x100 import runtime as x100
    for target,name in ((home,'register_properties'),(runtime,'register'),(x100,'register')):
        original=getattr(target,name)
        def fail(*a,**kw):
            original(*a,**kw)
            raise RuntimeError('partial register')
        with patch.object(target,name,fail):
            try: wfrl_blender.register()
            except RuntimeError: pass
            else: raise AssertionError('Partial registration accepted')
        assert not bpy.app.timers.is_registered(runtime.tick)
        assert not hasattr(bpy.types,'WFRL_PT_connection')
        assert not hasattr(bpy.types.Scene,'wfrl_channel_telemetry')
        assert not hasattr(bpy.types.WindowManager,'wfrl_home_page')
        wfrl_blender.register();wfrl_blender.unregister()
    print('WFRL_EXTENSION_LIFECYCLE_SMOKE=PASS')

if __name__ == '__main__': main()
