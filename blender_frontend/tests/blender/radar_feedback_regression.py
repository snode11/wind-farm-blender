"""Native materials, per-turbine events, icons, reload and missing data."""
import json
import os
from pathlib import Path
import sys
import tempfile
import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.environ.get('WFRL_TEST_RUNTIME', str(ROOT/'blender_frontend')))
if 'WFRL_TEST_RUNTIME' not in os.environ:
    sys.path.insert(0, str(ROOT))
import wfrl_blender
from wfrl_blender import clearance_replay, farm_flex, radar_feedback

wfrl_blender.register()
wfrl_blender.load_demo_scene(os.environ.get('WFRL_FARM_FLEX_PACKAGE'))
scene = bpy.context.scene
scene.wfrl_farm_panel_page = 'RADAR'


def seek(t):
    frame = 1 + (t-117)*60
    scene.frame_set(int(frame), subframe=frame-int(frame))


def check(t):
    seek(t)
    for tid, reader in farm_flex._ACTIVE.readers.items():
        expected = radar_feedback.beam_activity(reader, clearance_replay.sample(scene)['time_s'])
        for index, active in enumerate(expected, 1):
            obj = scene.objects[f'WFRL.Turbine.{tid}.ClearanceRadar.Beam{index}']
            assert obj['wfrl_beam_active'] == active, (tid,index,t)
            assert obj.data.materials[0].name == 'WFRL.ClearanceBeam.'+('Hit' if active else 'Idle')
            assert abs(obj.data.bevel_depth-(.030 if active else .018)) < 1e-7
    return radar_feedback.alarm_state(clearance_replay.sample(scene))


assert check(117) == 'waiting'
if farm_flex._ACTIVE.manifest.get('schema') == 'wfrl.farm-flex-review.v3':
    reader=farm_flex._ACTIVE.readers['T1']
    event=next(r for r in reader.package.measurements if r['beams']['B2']['valid'])
    stamp=event['time_s']
    expected_state='near_threshold' if event['beams']['B2']['estimate_m']<=7 else 'above_threshold'
    assert check(stamp)==expected_state
    measurement=clearance_replay.sample(scene)['measurement']
    assert measurement['time_s']==stamp
    assert scene.objects['WFRL.Turbine.T1.ClearanceRadar.Beam2']['wfrl_beam_active']
    # A later held card must not keep the beam orange past its event pulse.
    assert check(stamp+.3)==expected_state
    assert not scene.objects['WFRL.Turbine.T1.ClearanceRadar.Beam2']['wfrl_beam_active']
else:
    assert check(142.1) == 'above_threshold'
    stamp=166.85;expected_state='near_threshold'
    assert check(stamp)==expected_state
state=check(stamp)
scene.render.fps=24
clearance_replay.update(scene)
assert radar_feedback.alarm_state(clearance_replay.sample(scene))==state
for tid in ('T2','T3','all','T1'):
    bpy.ops.wfrl.farm_flex_view(turbine=tid)
    assert abs(clearance_replay.sample(scene)['time_s']-stamp)<1e-8
assert check(117)=='waiting'
assert check(stamp)==expected_state
if not bpy.app.background:
    assert all(radar_feedback.icon_id(name)>0 for name in radar_feedback.LABELS)
with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary)/'feedback.blend'
    seek(stamp)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene = bpy.context.scene
    assert check(stamp) == expected_state
clearance_replay.clear(scene,'test missing data')
assert radar_feedback.alarm_state(clearance_replay.sample(scene)) == 'waiting'
assert not any(obj.get('wfrl_beam_active', False) for obj in scene.objects)
wfrl_blender.load_demo_scene(os.environ.get('WFRL_FARM_FLEX_PACKAGE'))
scene = bpy.context.scene
assert check(117) == 'waiting'
result = dict(status='PASS', materials=True, independent_turbines=True, seek=True,
              clock=True, reload=True, clear=True)
Path(os.environ['WFRL_TEST_OUTPUT']).write_text(json.dumps(result,indent=2)+'\n')
print('RADAR_FEEDBACK_PASS',result)
wfrl_blender.unregister()
