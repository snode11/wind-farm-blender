"""Verify a CURRENT no-render preflight and reopen its saved scene. Never render."""
import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import bpy
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'projects/gw184-single'),str(ROOT/'blender_frontend')]
from gw184.model import read_config,motion_at
from gw184.pipeline import main,signature

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--preflight-dir',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
folder=args.preflight_dir.resolve()
g,m,c=read_config()
job=json.loads((folder/'job.json').read_text())
assert job['mode']=='preflight'
assert job==signature(SimpleNamespace(**job),(g,m,c)), 'Preflight is stale; regenerate from current code/config'
assert 'projects/gw184-single/geometry/defects.json' in job['code_and_sources']
assert 'blender_frontend/wfrl_blender/scene_builder.py' in job['code_and_sources']
assert 'blender_frontend/wfrl_blender/camera_projection.py' in job['code_and_sources']
report=json.loads((folder/'preflight.json').read_text())
assert report['rendered'] is False
assert not report['clearance']['colliding_poses']
assert not list(folder.rglob('*.png')) and not list(folder.rglob('*.mp4'))
# Every render mode must reject before scene construction or output creation.
with tempfile.TemporaryDirectory(prefix='gw184-no-render-guard-') as directory:
    for mode in ('stills','clip','full'):
        output=Path(directory)/mode
        with patch('gw184.pipeline.build',side_effect=AssertionError('Scene should not be built')):
            try:
                main(['--mode',mode,'--output',str(output)])
            except ValueError as exc:
                assert 'Rendering is disabled' in str(exc),str(exc)
            else:
                raise AssertionError('Render guard did not reject')
        assert not output.exists()

bpy.ops.wm.open_mainfile(filepath=str(folder/'GW184-single.blend'))
scene=bpy.context.scene
assert scene.frame_end==9600 and scene.render.fps==30
assert (scene.render.resolution_x,scene.render.resolution_y)==(3840,2160)
assert scene['render_authorized'] is False
assert not scene.render.use_motion_blur
camera_matrices={name:scene.objects[name].matrix_world.copy() for name in ('C1','C2','C3')}
for t in (0,10,55,100,160,220,265,310,319,320,55):
    scene.frame_set(round(t*30)+1)
    expected=motion_at(t,m)
    assert abs(math.degrees(scene.objects['GW184.Rotor'].rotation_euler.x)-expected['azimuth_deg'])<.002
    for i in (1,2,3):
        assert abs(math.degrees(scene.objects[f'GW184.Blade{i}.Pitch'].rotation_euler.z)-expected['pitch_deg'])<.0001
    assert all(scene.objects[name].matrix_world==matrix for name,matrix in camera_matrices.items())
for i,kind in ((1,'crack'),(2,'pit')):
    blade=scene.objects[f'GW184.Blade{i}']
    assert json.loads(blade['defect'])['kind']==kind
    assert blade.parent.name==f'GW184.Blade{i}.Pitch'
    assert any(p.material_index==1 for p in blade.data.polygons)
assert 'defect' not in scene.objects['GW184.Blade3']
print(json.dumps(dict(result='PASS',checks=['current source/config signature','all render modes blocked by default',
    'no rendered media in preflight','saved 4K scene and all three startup/shutdown pitch curves',
    'reverse seeking','fixed camera matrices','permanent blade-specific defects']),ensure_ascii=False))
