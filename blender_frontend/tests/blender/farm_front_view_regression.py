"""Check front-quarter framing and retained trails across camera-only switches."""
from pathlib import Path
import json
import os
import runpy
from types import SimpleNamespace
import bpy
from bpy_extras.object_utils import world_to_camera_view

ROOT=Path(__file__).resolve().parents[3]
loaded=runpy.run_path(os.environ.get('WFRL_FARM_LAUNCHER',str(ROOT/'scripts/blender/open_farm_flex.py')))
from wfrl_blender import tip_tracking,clearance_replay
from wfrl_blender.panels.farm_replay import draw_views
from wfrl_blender.panels import farm_replay
from unittest.mock import patch
scene=bpy.context.scene
out=Path(os.environ['WFRL_TEST_OUTPUT']);out.mkdir(parents=True,exist_ok=True)

class ViewButtons:
    """Capture actual panel bindings, including properties omitted by a button."""
    def __init__(self):
        self.buttons = {}
    def row(self, **kwargs):
        return self
    def operator(self, operator, *, text, **kwargs):
        props = SimpleNamespace()
        if operator == 'wfrl.farm_flex_view':
            self.buttons[text] = props
        return props
    def prop(self, *args, **kwargs):
        pass
    def label(self, **kwargs):
        pass
    def panel(self, *args, **kwargs):
        return self, None

layout = ViewButtons()
draw_views(layout, bpy.context)
expected = {'T1 Down': ('T1', 'DOWN'), 'T2 Down': ('T2', 'DOWN'),
            'T3 Down': ('T3', 'DOWN'), 'T1 侧前方': ('T1', 'FRONT'),
            '风场全景': ('all', 'DOWN')}
for label, (tid, angle) in expected.items():
    props = vars(layout.buttons[label])
    assert props == {'turbine': tid, 'angle': angle}, (label, props)

# The radar tab has its own buttons. Omitting angle here reuses the last
# FRONT UI invocation even though the button says Down.
radar_layout = ViewButtons()
scene.wfrl_farm_panel_page = 'RADAR'
with patch.object(farm_replay, 'draw_radar'), patch.object(farm_replay, 'draw_playback'):
    farm_replay.draw(radar_layout, bpy.context)
for tid in ('T1', 'T2', 'T3'):
    props = vars(radar_layout.buttons[tid + ' Down'])
    assert props == {'turbine': tid, 'angle': 'DOWN'}, props
    bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT')
    bpy.ops.wfrl.farm_flex_view(**props)
    assert scene.camera.name == f'WFRL.Camera.{tid}.Gimbal'
bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='DOWN')

for frame in range(1,181):
    scene.frame_set(frame)
trail=tip_tracking.active(scene)
before={bid:list(points) for bid,points in trail.points.items()}
time=clearance_replay.sample(scene)['time_s']
paused = bpy.context.screen.is_animation_playing
for label in ['T1 侧前方', 'T1 Down', 'T1 侧前方', 'T1 Down',
              '风场全景', 'T1 Down', 'T1 Down', 'T1 侧前方']:
    bpy.ops.wfrl.farm_flex_view(**vars(layout.buttons[label]))
    expected_camera = ('WFRL.Camera.T1.FrontQuarter' if label == 'T1 侧前方' else
                       'WFRL.Camera.FarmFlexOverview' if label == '风场全景' else
                       'WFRL.Camera.T1.Gimbal')
    assert scene.camera.name == expected_camera, (label, scene.camera.name)
    assert bpy.context.screen.is_animation_playing == paused
    assert scene.frame_current==180
    assert clearance_replay.sample(scene)['time_s']==time
    assert tip_tracking.active(scene) is trail
    assert {bid:list(points) for bid,points in trail.points.items()}==before
assert scene.camera.name=='WFRL.Camera.T1.FrontQuarter'
assert loaded['preview'].visible_turbines=={0}
assert all(not obj.hide_get() for obj in trail.objects.values())
# All blade vertices stay within the camera over the clip, including yaw changes.
limits=[]
for frame in (1,901,1801,2701,3601):
    scene.frame_set(frame);bpy.context.view_layer.update()
    coords=[]
    for bid in (1,2,3):
        blade=scene.objects[f'WFRL.Turbine.T1.Blade{bid}']
        coords.extend(world_to_camera_view(scene,scene.camera,blade.matrix_world@v.co) for v in blade.data.vertices)
    bounds=[min(p.x for p in coords),max(p.x for p in coords),min(p.y for p in coords),max(p.y for p in coords)]
    assert bounds[0]>.04 and bounds[1]<.96 and bounds[2]>.04 and bounds[3]<.96,bounds
    limits.append(bounds)
# Changing the turbine still starts with a fresh, correctly posed sample.
bpy.ops.wfrl.farm_flex_view(turbine='T2')
assert tip_tracking.active(scene).turbine_id=='T2'
assert all(len(points)==1 for points in tip_tracking.active(scene).points.values())
bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='FRONT')
scene.frame_set(1)
for frame in range(2,541):scene.frame_set(frame)
assert all(len(points)<=540 for points in tip_tracking.active(scene).points.values())
result=dict(status='PASS',camera=scene.camera.name,retained_samples_per_blade=len(before[1]),bounds=limits)
(out/'checks.json').write_text(json.dumps(result,indent=2))
if os.environ.get('WFRL_RENDER_FRONT'):
    scene.render.resolution_x=1280;scene.render.resolution_y=720;scene.render.resolution_percentage=100
    scene.render.filepath=str(out/'front-quarter.png')
    bpy.ops.render.render(write_still=True)
print('FARM_FRONT_VIEW_PASS',json.dumps(result),flush=True)
