"""Verify the self-contained MAPPO demo, transport, telemetry and saved replay."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sys
import tempfile
import bpy
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.environ.get('WFRL_TEST_RUNTIME', str(ROOT / 'blender_frontend')))
if 'WFRL_TEST_RUNTIME' not in os.environ:
    sys.path.insert(0, str(ROOT))
import wfrl_blender
from wfrl_blender import farm_flex, clearance_replay, charts, runtime
wfrl_blender.register()
assert bpy.ops.wfrl.load_demo() == {'FINISHED'}
scene = bpy.context.scene
assert scene.frame_end == 3601 and scene.render.fps == 60
assert runtime.get_state().connection == 'OFFLINE RESULTS'
assert hasattr(bpy.types, 'WFRL_PT_FarmFlex')
assert not hasattr(bpy.types, 'WFRL_PT_demo_panel')
assert len(farm_flex._ACTIVE.blades) == 9
scene.frame_set(901)
stamp = clearance_replay.sample(scene)['time_s']
for tid in ('T1','T2','T3'):
    for channel in ('yaw','pitch','rotor_speed','power','torque','load','pitch_command'):
        point = charts.history.points(tid, channel)[-1]
        assert point.time == stamp and point.fidelity == 'FAST.Farm'
        assert point.value is not None
    assert charts.history.points(tid, 'reward')[-1].value is None
assert abs(charts.history.points(charts.FARM, 'power')[-1].value - sum(charts.history.points(t, 'power')[-1].value for t in ('T1','T2','T3'))) < 1e-8
before = charts.raw_history.snapshot()
scene.frame_set(901)
assert charts.raw_history.snapshot() == before
bpy.ops.wfrl.farm_transport(action='STOP')
assert scene.frame_current == 901
bpy.ops.wfrl.farm_transport(action='STEP')
assert scene.frame_current == 902
assert abs(clearance_replay.sample(scene)['time_s'] - stamp - 1/60) < 1e-8
scene.render.fps = 24
assert abs(clearance_replay.sample(scene)['time_s'] - stamp - 1/60) < 1e-8
scene.render.fps = 60
for camera in ('World','Top','Side'):
    bpy.ops.wfrl.select_camera(camera_name='WFRL.Camera.' + camera)
    assert farm_flex._ACTIVE.visible_turbines == {0,1,2}
    assert scene.frame_current == 902
bpy.ops.wfrl.farm_transport(action='RESET')
assert scene.frame_current == 1
assert len(charts.history.points('T1','power')) == 1
scene.frame_set(scene.frame_end)
assert clearance_replay.sample(scene)['time_s'] == 177
assert charts.history.points('T1','rotor_speed')[-1].value > 0
bpy.ops.wfrl.farm_transport(action='STEP')
assert scene.frame_current == scene.frame_end
# All expanded UI sections resolve their properties/operators, with no SYNTH demo panel.
from wfrl_blender.panels import farm_replay
class Layout:
    def __init__(self): self.ops=[]; self.labels=[]
    def panel(self, *a, **kw): return self,self
    def label(self, *, text='', **kw): self.labels.append(text)
    def row(self, **kw): return self
    def column(self, **kw): return self
    def box(self): return self
    def prop(self, owner, name, **kw):
        if not name.startswith('['): assert hasattr(owner, name),name
    def operator(self, name, **kw):
        namespace,op = name.split('.')
        getattr(getattr(bpy.ops, namespace), op).get_rna_type()
        self.ops.append(name)
        return SimpleNamespace()
layout=Layout()
for page in ('DEFLECTION', 'RADAR', 'TOOLS'):
    stamp = clearance_replay.sample(scene)['time_s']
    camera = scene.camera
    scene.wfrl_farm_panel_page = page
    farm_replay.draw(layout,bpy.context)
    assert clearance_replay.sample(scene)['time_s'] == stamp
    assert scene.camera == camera
scene.wfrl_farm_panel_page = 'DEFLECTION'
assert 'wfrl.export_history' in layout.ops and 'wfrl.capture_recording' in layout.ops
with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'replay.blend'
    scene.frame_set(501)
    scene.wfrl_farm_panel_page = 'RADAR'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene=bpy.context.scene
    assert farm_flex.is_active(scene)
    assert scene.frame_current == 501
    assert scene.wfrl_farm_panel_page == 'RADAR'
    assert runtime.get_state().connection == 'OFFLINE RESULTS'
    assert bpy.ops.wfrl.farm_flex_view(turbine='T3') == {'FINISHED'}
    # Missing packages clear the old reader and recover via the bundled demo.
    try: wfrl_blender.load_demo_scene(Path(directory)/'missing')
    except OSError: pass
    else: raise AssertionError('Missing package was accepted')
    assert clearance_replay.reader_for(scene) is None
    assert charts.history.points('T1','power') == ()
    assert bpy.ops.wfrl.load_demo() == {'FINISHED'}
    assert farm_flex.is_active(scene)
    assert not any(o.name.endswith('.001') and 'FarmFlexOverview' in o.name for o in scene.objects)
wfrl_blender.unregister()
assert farm_flex._ACTIVE is None
assert not hasattr(bpy.types,'WFRL_PT_FarmFlex')
print('MAPPO_COMPLETE_DEMO_PASS', json.dumps(dict(duration_s=60,blades=9,telemetry=True,saved_replay=True,recovery=True)))
