"""Native scene ownership, DisXY validity and full-FOV preview contracts."""
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
sys.dont_write_bytecode = True
import wfrl_blender as addon
from wfrl_blender import custom_camera_preview as preview, custom_cameras as cameras
from wfrl_blender import runtime, wake
from wfrl_blender.animation import KinematicState
from wfrl_blender.scene_model import SceneDTO
from wfrl_blender.state import FrontendState


def record(value, unit):
    return dict(value=value, unit=unit, validity='valid', error=None, fidelity='DIRECT',
                provenance=dict(backend_session='native-test', channel='native-test'),
                source_age_seconds=0., stale_after_seconds=2.)


addon.register()
other = bpy.context.scene
owner = bpy.data.scenes.new('Live owner')
other_objects = set(other.objects.keys())
other_properties = dict(other.items())
scene_data = dict(name='native', backend='floris', turbine='nrel5mw', dt=.1,
                  layout=[dict(id='T1', x=0., y=0.)], inflow=dict(speed=8., direction=270.))
runtime._backend_scene = owner
runtime._build_backend_scene(SceneDTO.from_mapping(scene_data))
assert bpy.context.scene == other
assert owner.get('wfrl_scene_kind') == 'live'
assert set(other.objects.keys()) == other_objects
assert dict(other.items()) == other_properties

runtime._live_scene = SceneDTO.from_mapping(scene_data)
runtime._state = FrontendState(mode='replay', connection='CONNECTED', run_status='PAUSED',
                               confirmed=True, session_id='native-test')
runtime.kinematics = KinematicState()
runtime._last_tick = runtime._last_display_state = runtime._last_async_display_state = None
payload = dict(scene=scene_data, step=1, timestamp=dict(value=2., timebase='simulation_seconds'),
               mode='replay', farm={}, turbines=[dict(turbine_id='T1', channels=dict(
                   yaw=record(12., 'deg'), pitch=record(3., 'deg'),
                   rotor_speed=record(8., 'rpm'), power=record(1., 'MW')))])
messages = [dict(type='snapshot', sequence=1, payload=payload)]
client = SimpleNamespace(poll=lambda: [messages.pop(0)] if messages else [],
                         last_error='', status='CONNECTED', session_id='native-test',
                         progress='Connected', port=8765)
client.close = lambda: setattr(client, 'status', 'DISCONNECTED')
client.connect = lambda: setattr(client, 'progress', 'Connecting')
runtime._client = client
runtime.tick()
assert owner['wfrl_telemetry_time_s'] == 2.
assert owner['wfrl_power_mw'][0] == 1.
assert dict(other.items()) == other_properties

owner.wfrl_wake_display = 'CINEMATIC'
messages.append(dict(type='snapshot', sequence=2, payload=payload))
runtime.tick()
assert runtime._state.connection == 'CONNECTED'
assert any(obj.name.startswith('WFRL.Cinematic.') and not obj.hide_render for obj in owner.objects)
owner.wfrl_wake_display = 'SCIENTIFIC'

data = record(dict(kind='disxy', shape=[2, 2], values=[1., 2., 3., 4.],
                   x=[0., 1.], y=[0., 1.], z=90.), 'm/s')
data['stale_after_seconds'] = 5.
wake_payload = dict(encoding='json', turbine_ids=['T1'], data=data)
frame = wake.validate_wake_payload(wake_payload)
obj = wake.apply_disxy_frame(frame, scene=owner)
assert obj in owner.objects.values() and obj not in other.objects.values()
assert not obj.hide_render
runtime.latest_wake = wake.validate_wake_payload(wake_payload, received_monotonic=time.monotonic()-6.)
runtime.tick()
assert obj['validity'] == 'stale' and obj.hide_render
wake.apply_disxy_frame(wake.validate_wake_payload(wake_payload), scene=owner)
assert obj['validity'] == 'valid' and not obj.hide_render
owner.wfrl_show_disxy = False
wake.apply_disxy_frame(wake.validate_wake_payload(wake_payload), scene=owner)
assert obj.hide_render
owner.wfrl_show_disxy = True
assert not obj.hide_render
owner.wfrl_show_disxy = False
wake.set_disxy_validity(owner, 'stale')
owner.wfrl_show_disxy = True
assert obj.hide_render
wake.apply_disxy_frame(wake.validate_wake_payload(wake_payload), scene=owner)
assert not obj.hide_render

secondary_layer = owner.view_layers.new('Secondary')
obj.hide_set(True, view_layer=secondary_layer)
wake.set_disxy_validity(owner, 'stale')
owner.wfrl_show_lidar = not owner.wfrl_show_lidar
wake.apply_disxy_frame(wake.validate_wake_payload(wake_payload), scene=owner)
assert not obj.hide_get(view_layer=owner.view_layers[0])
assert obj.hide_get(view_layer=secondary_layer)

camera = bpy.data.objects.new('Wide camera', bpy.data.cameras.new('Wide camera optics'))
owner.collection.objects.link(camera)
params = cameras.CameraParameters((1., 1., 90.), fov=90., vfov=4.)
cameras.apply_parameters(camera, params)
with bpy.context.temp_override(scene=owner, view_layer=owner.view_layers[0]):
    owner.view_layers[0].update()
    graph = bpy.context.evaluated_depsgraph_get()
    assert preview.matrices(camera, graph, 320, 4096, True)[:2] == (444, 16)
    assert preview.matrices(camera, graph, 1920, 4096)[:2] == (1920, 67)
    assert cameras.parameters(camera) == params

runtime.latest_wake = None
bpy.data.scenes.remove(owner)
runtime.tick()
assert runtime._state.connection == 'DISCONNECTED' and not runtime._state.confirmed
assert dict(other.items()) == other_properties
runtime.connect(8765)
assert runtime._backend_scene == other and runtime._live_scene is None
assert runtime._state.session_id == client.session_id == 'native-test'
addon.unregister()
result = dict(status='PASS', blender=bpy.app.version_string, background=bpy.app.background,
              scope='Native RNA, scene override, data ownership, validity and projection; no GPU pixels or FPS')
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)
(output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print('LIVE_CONTRACTS_PASS', flush=True)
