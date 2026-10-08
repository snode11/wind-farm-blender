"""Check real mesh coordinates, UI state, seeks, hidden T1 and reload cleanup.

WFRL_ADDON_MODULE and WFRL_EXPECTED_ADDON_ROOT select and verify an installed
extension. Only bare-module mode prepends the source/runtime paths.
"""
from pathlib import Path
import importlib
import json
import os
import sys
import tempfile
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path.insert(0, os.environ.get('WFRL_TEST_RUNTIME', str(ROOT / 'blender_frontend')))
    if 'WFRL_TEST_RUNTIME' not in os.environ:
        sys.path.insert(0, str(ROOT))
wfrl_blender = importlib.import_module(MODULE)
farm_flex = importlib.import_module(MODULE + '.farm_flex')
clearance_replay = importlib.import_module(MODULE + '.clearance_replay')
rigid_frame = importlib.import_module(MODULE + '.deflection').rigid_frame
ADDON_FILE = str(Path(wfrl_blender.__file__).resolve())
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(ADDON_FILE).parent == Path(expected_root).resolve(), ADDON_FILE
    assert Path(farm_flex.__file__).resolve().parent == Path(expected_root).resolve(), farm_flex.__file__
print('TIP_DEFLECTION_ADDON', json.dumps({'module': MODULE, 'file': ADDON_FILE}), flush=True)
wfrl_blender.register()
bpy.ops.wfrl.load_demo()
scene = bpy.context.scene
view = farm_flex._ACTIVE.comparison
assert view is not None and len(view.objects) == 7
# The demo intentionally starts with its comparison overlay hidden. Explicitly
# enable it before checking marker switching and hide/show behavior.
scene.wfrl_deflection_visible = True
initial_apex = farm_flex._ACTIVE.blades[0][0].data.vertices[-1].co.copy()
saved_errors, interpolated_errors, source_errors, mesh_transport_errors = [], [], [], []
for frame in sorted(set([1, 2, 3, 4, 901, 1801, 3600, 3601] + list(range(1, 3602, 90)))):
    scene.frame_set(frame)
    for b, row in view.rows.items():
        obj = farm_flex._ACTIVE.blades[b - 1][0]
        actual = np.asarray(obj.matrix_world @ obj.data.vertices[-2].co)
        np.testing.assert_allclose(row['actual'], actual, rtol=0, atol=1e-8)
        assert row['time'] == clearance_replay.sample(scene)['time_s']
        np.testing.assert_allclose(row['components'], row['axes'].T @ (actual - row['reference']), atol=1e-8)
        (interpolated_errors if row['interpolated'] else saved_errors).append(row['error'])
        if not row['interpolated']:
            # Independently transport the ideal unloaded structural point using
            # the saved terminal-section transform, bypassing the Blender mesh.
            # This separates source surface/structural-channel disagreement from
            # the display's float32 local vertices and world matrix arithmetic.
            owner = farm_flex._ACTIVE
            frame_i = int(np.argmin(abs(owner.times - row['time'])))
            hub0, axes0, pitched0 = rigid_frame(view.data['scalars'], [0] * 6, b)
            rest = (hub0 + pitched0 @ view.data['tip_local_m'] if 'tip_local_m' in view.data
                    else hub0 + axes0[:, 2] * view.data['scalars']['TipRad'])
            transform = owner.transforms[frame_i, 0, b - 1, -1].astype(np.float64)
            source_actual = transform[:, :3] @ rest + transform[:, 3]
            source_errors.append(row['axes'].T @ (source_actual - row['reference']) - row['simulation'])
            mesh_transport_errors.append(actual - source_actual)
saved_max = np.max(np.abs(saved_errors), axis=0)
interpolated_max = np.max(np.abs(interpolated_errors), axis=0)
source_max = np.max(np.abs(source_errors), axis=0)
mesh_transport_max = np.max(np.abs(mesh_transport_errors), axis=0)
# Do all UI/lifecycle checks before reporting the unchanged physical gate. A
# source precision failure must not prevent those software checks from running.
assert max(mesh_transport_max) < .00005, mesh_transport_max
scene.frame_set(1)
np.testing.assert_array_equal(initial_apex, farm_flex._ACTIVE.blades[0][0].data.vertices[-1].co)
for b in ('1', '2', '3'):
    scene.wfrl_deflection_blade = b
    np.testing.assert_allclose(view.markers['Actual'].location, view.rows[int(b)]['actual'], atol=1e-5)
scene.wfrl_deflection_visible = False
assert all(o.hide_get() and o.hide_render for o in view.objects)
scene.wfrl_deflection_visible = True
assert not any(o.hide_get() for o in view.objects)
before = scene.frame_current
assert bpy.ops.wfrl.deflection_view() == {'FINISHED'}
assert scene.frame_current == before and scene.camera.name == 'WFRL.Camera.T1.TipComparison'
camera_position = scene.camera.location.copy()
scene.frame_set(181)
assert (scene.camera.location - camera_position).length > 1
scene.wfrl_deflection_visible = False
camera_position = scene.camera.location.copy()
scene.frame_set(271)
assert (scene.camera.location - camera_position).length > 1
assert all(o.hide_get() for o in view.objects)
scene.wfrl_deflection_visible = True
bpy.ops.wfrl.farm_flex_view(turbine='T2')
scene.frame_set(902)
assert all(o.hide_get() for o in view.objects)
assert view.rows[1]['time'] == clearance_replay.sample(scene)['time_s']
hidden = view.rows[1]['actual'].copy()
bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT')
np.testing.assert_array_equal(view.rows[1]['actual'], hidden)
assert not any(o.hide_get() for o in view.objects)
scene.render.fps = 24
scene.frame_set(902)
assert view.rows[1]['time'] == 117 + 901 / 60
scene.render.fps = 60
scene.wfrl_deflection_blade = '1'
assert bpy.ops.wfrl.deflection_view() == {'FINISHED'}
with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / 'deflection.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene = bpy.context.scene
    view = farm_flex._ACTIVE.comparison
    assert len([o for o in scene.objects if o.name.startswith('WFRL.Deflection.T1.')]) == 7
    assert view.rows[1]['time'] == 117 + 901 / 60
    assert scene.camera.name == 'WFRL.Camera.T1.TipComparison'
    bpy.ops.wfrl.load_demo()
    assert len([o for o in bpy.context.scene.objects if o.name.startswith('WFRL.Deflection.T1.')]) == 7
report = dict(addon_module=MODULE, addon_file=ADDON_FILE,
              verification_layer='INSTALLED_EXTENSION' if MODULE.startswith('bl_ext.') else 'SOURCE_RUNTIME',
              saved_mesh_max_error_m=saved_max.tolist(), interpolated_mesh_max_error_m=interpolated_max.tolist(),
              independent_source_max_error_m=source_max.tolist(), mesh_transport_max_error_m=mesh_transport_max.tolist(),
              saved_gate_threshold_m=.0002, interpolated_gate_threshold_m=.005,
              physical_gate='PASS' if max(saved_max) < .0002 and max(interpolated_max) < .005 else 'FAILED_GATE',
              software_checks='PASS', switching=True, seeking=True, hidden_t1=True, fixed_clock=True, reload=True)
wfrl_blender.unregister()
assert farm_flex._ACTIVE is None
assert not [o for o in bpy.context.scene.objects if o.name.startswith('WFRL.Deflection.T1.')]
if os.environ.get('WFRL_TEST_OUTPUT'):
    Path(os.environ['WFRL_TEST_OUTPUT']).write_text(json.dumps(report, indent=2))
print('TIP_DEFLECTION_RESULT', json.dumps(report), flush=True)
assert max(saved_max) < .0002, saved_max
assert max(interpolated_max) < .005, interpolated_max
print('TIP_DEFLECTION_PASS', json.dumps(report))
