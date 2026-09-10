"""Exercise modal routing with native Blender scene/area and event-shaped inputs."""
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wfrl_blender
wfrl_blender.register()
from wfrl_blender.panels import gimbal
def run():
    try:
        scene = bpy.context.scene
        for t in ('T1', 'T2', 'T3'):
            root = bpy.data.objects.new(f'WFRL.Turbine.{t}.YawRoot', None)
            scene.collection.objects.link(root)
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(area=area, region=region):
            assert bpy.ops.wfrl.gimbal_mode('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
            op = gimbal._ACTIVE
            def event(kind, value='PRESS', x=150, y=150):
                return SimpleNamespace(type=kind, value=value, mouse_x=region.x+x, mouse_y=region.y+y)
            camera = gimbal.current(scene)
            initial = camera.location.copy()
            assert op.modal(bpy.context, event('LEFTMOUSE')) == {'RUNNING_MODAL'}
            op.modal(bpy.context, event('MOUSEMOVE', x=190, y=200))
            op.modal(bpy.context, event('LEFTMOUSE', 'RELEASE', x=190, y=200))
            assert camera['gimbal_yaw'] != 180 and camera['gimbal_pitch'] > -90
            op.modal(bpy.context, event('WHEELUPMOUSE'))
            assert camera['gimbal_fov'] == 72
            scene.wfrl_gimbal_turbine = 'T2'
            assert area.spaces.active.camera.name == 'WFRL.Camera.T2.Gimbal'
            assert op.modal(bpy.context, event('MOUSEMOVE')) != {'FINISHED'}
            assert gimbal._ACTIVE is op
            assert bpy.ops.wfrl.gimbal_preset(preset='FRONT') == {'FINISHED'}
            camera2 = gimbal.current(scene)
            cx, cy, radius = op.geometry()
            op.modal(bpy.context, event('LEFTMOUSE', x=cx+radius/2, y=cy))
            op._last_time -= .05
            op.modal(bpy.context, event('TIMER'))
            assert camera2['gimbal_yaw'] < 180
            op.modal(bpy.context, event('LEFTMOUSE', 'RELEASE', x=cx+radius/2, y=cy))
            yaw = camera2['gimbal_yaw']
            op.modal(bpy.context, event('TIMER'))
            assert camera2['gimbal_yaw'] == yaw, 'Joystick must stop on release'
            assert (camera.location-initial).length == 0
            assert op.modal(bpy.context, event('ESC')) == {'FINISHED'}
            assert gimbal._ACTIVE is None and op._timer is None and op._handle is None
        wfrl_blender.unregister()
        print('GIMBAL_CONTROLS_SMOKE_OK')
    except Exception:
        import traceback
        traceback.print_exc()
        Path('/tmp/wfrl-gimbal-control-result.txt').write_text('FAIL')
    else:
        Path('/tmp/wfrl-gimbal-control-result.txt').write_text('PASS')
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(run, first_interval=1)
