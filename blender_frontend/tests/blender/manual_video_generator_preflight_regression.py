"""Execute the video generator through native preparation without rendering."""
import importlib
import json
import os
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
video = importlib.import_module('wfrl_blender.manual_video')
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)


class LaunchReached(Exception):
    pass


def stop_at_launch(scene, output):
    assert scene.wfrl_manual_display_detail == 'CLOSEUP'
    assert all(len(getattr(entry, f'blade{i}_object').data.vertices) == 15361
               for entry in scene.wfrl_manual_turbines for i in (1, 2, 3))
    assert output == OUT / 'not-rendered.mp4'
    raise LaunchReached


try:
    with patch.object(sys, 'argv', ['generate_manual_pitch_video.py', '--', '--output',
                                  str(OUT / 'not-rendered.mp4'), '--detach']), \
         patch.object(video, 'launch', side_effect=stop_at_launch):
        try:
            runpy.run_path(str(ROOT / 'scripts/blender/generate_manual_pitch_video.py'),
                           run_name='__main__')
        except LaunchReached:
            pass
        else:
            raise AssertionError('Video generator did not reach its launch boundary')
    assert not (OUT / 'not-rendered.mp4').exists()
    result = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
                  blade_count=9, vertices_per_blade=15361, render_started=False,
                  scope='Native generator preparation and frozen trial; media output is not exercised.')
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
finally:
    addon.unregister()
print('MANUAL_VIDEO_GENERATOR_PREFLIGHT_PASS', flush=True)
