"""Tower fittings must follow their support, including seeks and saved scenes."""
from pathlib import Path
import json
import os
import sys
import tempfile

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.environ.get('WFRL_TEST_RUNTIME', str(ROOT / 'blender_frontend')))
if 'WFRL_TEST_RUNTIME' not in os.environ:
    sys.path.insert(0, str(ROOT))
import wfrl_blender
from wfrl_blender import farm_flex, clearance_replay
from wfrl_blender.tower_motion import interpolate_transform
from wfrl_blender.mechanical_details import recess_yaw_seal

wfrl_blender.register()
package = Path(os.environ.get('WFRL_FARM_FLEX_PACKAGE', farm_flex.default_package()))
wfrl_blender.load_demo_scene(package)
scene = bpy.context.scene
specs = (('YawSeal', 87.6), ('TowerWeld28', 28.), ('TowerWeld56', 56.))
checks = []

# Migrate an old saved seal, then prove its mesh is stable on repeated load.
seal = scene.objects['WFRL.Turbine.T1.YawSeal']
del seal['wfrl_seal_geometry_revision']
for vertex in seal.data.vertices:
    vertex.co.x *= 1.04
    vertex.co.y *= 1.04
recess_yaw_seal(seal, 87.6)
saved = np.array([v.co[:] for v in seal.data.vertices])
recess_yaw_seal(seal, 87.6)
np.testing.assert_array_equal(saved, [v.co[:] for v in seal.data.vertices])


def check(frame):
    scene.frame_set(frame)
    active = farm_flex._ACTIVE
    t = clearance_replay.sample(scene)['time_s']
    i = int(np.clip(np.searchsorted(active.times, t, side='right') - 1, 0, len(active.times) - 2))
    a = (t - active.times[i]) / (active.times[i + 1] - active.times[i])
    support = active.tower_motion
    transforms = support['transforms'][i] * (1 - a) + support['transforms'][i + 1] * a
    for k, tid in enumerate(active.readers):
        for suffix, height in specs:
            obj = scene.objects[f'WFRL.Turbine.{tid}.{suffix}']
            z = support['heights']
            j = int(np.clip(np.searchsorted(z, height) - 1, 0, len(z) - 2))
            f = (height - z[j]) / (z[j + 1] - z[j])
            tr = interpolate_transform(transforms[k, j], transforms[k, j + 1], f)
            vertices = np.array([v.co[:] for v in obj.data.vertices])
            if suffix == 'YawSeal':
                assert np.linalg.norm(vertices[:, :2], axis=1).max() < 1.899
            expected = vertices @ tr[:, :3].T + tr[:, 3] + active.manifest['layout_m'][k]
            matrix = np.array(obj.matrix_world)
            actual = vertices @ matrix[:3, :3].T + matrix[:3, 3]
            error = float(np.max(np.linalg.norm(actual - expected, axis=1)))
            checks.append(dict(frame=frame, object=obj.name, max_error_m=error))
            assert error < .0002, checks[-1]
    return {f'{tid}.{suffix}': np.array(scene.objects[f'WFRL.Turbine.{tid}.{suffix}'].matrix_world)
            for tid in active.readers for suffix, _ in specs}


# Includes both reported screenshot times and backward/repeated seeks.
reference = check(2085)
for frame in (2280, 1, 3601, 2, 901, 2085, 2085):
    result = check(frame)
for key in reference:
    np.testing.assert_array_equal(result[key], reference[key])
with tempfile.TemporaryDirectory() as temp:
    dest = Path(temp) / 'fittings.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(dest))
    bpy.ops.wm.open_mainfile(filepath=str(dest))
    scene = bpy.context.scene
    result = check(2085)
    for key in reference:
        np.testing.assert_array_equal(result[key], reference[key])
    check(2280)

active = farm_flex._ACTIVE
clearance_replay.clear(scene, 'fitting restore test')
scene.frame_set(10)
bpy.context.view_layer.update()
for tid in active.readers:
    for suffix, _ in specs:
        obj = scene.objects[f'WFRL.Turbine.{tid}.{suffix}']
        np.testing.assert_allclose(obj.location, (0, 0, 0), atol=1e-7)
        np.testing.assert_allclose(obj.rotation_euler, (0, 0, 0), atol=1e-7)
wfrl_blender.load_demo_scene(package)
scene = bpy.context.scene
check(2280)
result = dict(status='PASS',checks=len(checks),max_error_m=max(row['max_error_m'] for row in checks),
              seeks=True,repeat=True,save_reload=True,clear_restore=True,rebuild=True)
Path(os.environ['WFRL_TEST_OUTPUT']).write_text(json.dumps(result, indent=2) + '\n')
print('TOWER_FITTINGS_PASS', json.dumps(result), flush=True)
wfrl_blender.unregister()
