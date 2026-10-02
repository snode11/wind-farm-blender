"""Recorded-flex numerical/visibility/seek checks for the frontend optimizations."""
import importlib
import json
import os
from pathlib import Path
import sys
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
flex = importlib.import_module(MODULE+'.farm_flex')
replay = importlib.import_module(MODULE+'.clearance_replay')
addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
view = flex._ACTIVE.comparison
checks = 0
for visible in (False, True, False):
    scene.wfrl_deflection_visible = visible
    for frame in (1, 2, 902, 1801, 3600, 117, 902):
        scene.frame_set(frame)
        for blade in ('1', '2', '3'):
            scene.wfrl_deflection_blade = blade
            row = view.rows[int(blade)]
            obj = flex._ACTIVE.blades[int(blade)-1][0]
            actual = np.asarray(obj.matrix_world @ obj.data.vertices[-2].co)
            np.testing.assert_allclose(row['actual'], actual, rtol=0, atol=1e-8)
            np.testing.assert_allclose(row['components'], row['axes'].T @ (actual-row['reference']), atol=1e-8)
            assert row['time'] == replay.sample(scene)['time_s']
            assert all(o.hide_get() == (not visible) and o.hide_render == (not visible) for o in view.objects)
            if visible:
                np.testing.assert_allclose(view.markers['Actual'].location, actual, atol=1e-5)
            checks += 1
view.objects[0].hide_set(False)
view.objects[0].hide_render = False
scene.frame_set(903)
assert view.objects[0].hide_get() and view.objects[0].hide_render
stamp = replay.sample(scene)['time_s']
scene.render.fps = 24
scene.frame_set(903)
assert replay.sample(scene)['time_s'] == stamp
result = {'status': 'PASS', 'module': addon.__file__, 'checks': checks,
          'scenarios': ['hidden and visible numerical rows', 'all selected blades',
                        'forward/backward seeking', 'external visibility edit recovery',
                        'fixed simulation clock after FPS change'], 'blender': bpy.app.version_string}
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)
(output/'result.json').write_text(json.dumps(result, indent=2))
print('FRONTEND_RUNTIME_PASS', json.dumps(result), flush=True)
