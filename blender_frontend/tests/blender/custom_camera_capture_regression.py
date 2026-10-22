"""Native camera/config guards; no GPU or installed-extension claim."""
from dataclasses import replace
import json
import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT/'blender_frontend')]
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE+'.custom_cameras')
capture = importlib.import_module(MODULE+'.custom_camera_capture')
preview = importlib.import_module(MODULE+'.custom_camera_preview')
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve(), addon.__file__

addon.register()
scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
root = bpy.data.objects.new(core.ROOT_NAME, None)
scene.collection.objects.link(root)
bpy.ops.mesh.primitive_cube_add(size=2)
shell = bpy.context.object
shell.name, shell.parent = core.SURFACE_NAMES[0], root
bpy.context.view_layer.update()
cam = core.begin_draft(scene, 1)
params = core.CameraParameters((0, 0, 1.1), 123, -45, 37, 90, 60, None, 640, .006, 2000)
core.apply_parameters(cam, params, core.validate_position(scene, params.location)[0])
core.commit_draft(scene, 1, cam)
bpy.context.view_layer.update()
assert not capture.preflight(bpy.context)['errors']
checks = {'canonical_pose_accepted': True}
layout = core.layout_dict(scene)
layout_hash = core.layout_hash(layout)
native_hash = core.live_layout_hash(scene)

def expect_rejected(change, restore):
    change()
    bpy.context.view_layer.update()
    assert core.layout_hash(core.layout_dict(scene)) == layout_hash
    errors = capture.preflight(bpy.context)['errors']
    assert any('原生相机状态与保存配置不一致' in error for error in errors), errors
    restore()
    bpy.context.view_layer.update()
    assert not capture.preflight(bpy.context)['errors']

expect_rejected(lambda: setattr(cam, 'location', (0, 0, 1.2)),
                lambda: core.apply_parameters(cam, params))
expect_rejected(lambda: setattr(cam, 'rotation_euler', (0, 0, 0)),
                lambda: core.apply_parameters(cam, params))
expect_rejected(lambda: setattr(cam, 'delta_location', (0, 0, .1)),
                lambda: setattr(cam, 'delta_location', (0, 0, 0)))
expect_rejected(lambda: setattr(cam, 'scale', (1, 1, 1.1)),
                lambda: setattr(cam, 'scale', (1, 1, 1)))
expect_rejected(lambda: setattr(cam, 'matrix_parent_inverse', Matrix.Translation((0, 0, .1))),
                lambda: cam.matrix_parent_inverse.identity())
expect_rejected(lambda: setattr(cam.data, 'lens', cam.data.lens + 1),
                lambda: core.apply_parameters(cam, params))
expect_rejected(lambda: setattr(cam.data, 'sensor_width', 40),
                lambda: core.apply_parameters(cam, params))
expect_rejected(lambda: setattr(cam.data, 'shift_x', .1),
                lambda: core.apply_parameters(cam, params))
expect_rejected(lambda: setattr(cam.data, 'clip_start', .01),
                lambda: core.apply_parameters(cam, params))
checks['native_position_rotation_delta_scale_parent_inverse_lens_sensor_shift_clip_rejected'] = True
constraint = cam.constraints.new('COPY_LOCATION')
constraint.target = root
bpy.context.view_layer.update()
assert any('约束' in error for error in capture.preflight(bpy.context)['errors'])
cam.constraints.remove(constraint)
bpy.context.view_layer.update()
assert not capture.preflight(bpy.context)['errors']
checks['evaluated_constraint_pose_rejected'] = True
driver = cam.data.driver_add('lens').driver
driver.expression = str(cam.data.lens + 1)
bpy.context.view_layer.update()
assert any('镜头或裁剪' in error for error in capture.preflight(bpy.context)['errors'])
cam.data.driver_remove('lens')
core.apply_parameters(cam, params)
bpy.context.view_layer.update()
assert not capture.preflight(bpy.context)['errors']
checks['evaluated_lens_driver_rejected'] = True

# NaN comparisons never exceed a tolerance. Check finite native values before
# comparing poses/lenses, and return an actionable preflight error before BVH
# geometry or JSON hashing sees the corrupted state.
for change, restore in (
        (lambda: setattr(cam, 'delta_location', (float('nan'), 0, 0)),
         lambda: setattr(cam, 'delta_location', (0, 0, 0))),
        (lambda: setattr(cam.data, 'lens', float('nan')),
         lambda: core.apply_parameters(cam, params)),
        (lambda: setattr(cam.data, 'clip_end', float('nan')),
         lambda: core.apply_parameters(cam, params)),
        (lambda: setattr(root, 'location', (float('nan'), 0, 0)),
         lambda: setattr(root, 'location', (0, 0, 0)))):
    change()
    bpy.context.view_layer.update()
    errors = capture.preflight(bpy.context)['errors']
    assert any('非有限数值' in error for error in errors), errors
    restore()
    bpy.context.view_layer.update()
    assert not capture.preflight(bpy.context)['errors']
checks['nan_delta_lens_clip_parent_pose_rejected_before_geometry_and_hashing'] = True

# Common parent replay motion changes camera world pose but not its saved local
# configuration, and must remain valid before/after a capture sample.
root.location, root.rotation_euler = (1350, -2700, 98), (.01, -.02, 1.1)
bpy.context.view_layer.update()
assert core.live_layout_hash(scene) == native_hash
assert not capture.preflight(bpy.context)['errors']
checks['common_parent_motion_accepted'] = True

job = capture.Capture.__new__(capture.Capture)
job.scene, job.window = scene, SimpleNamespace(screen=SimpleNamespace(is_animation_playing=False))
job.expected_frame = (scene.frame_current, scene.frame_subframe)
job.layout_hash, job.live_layout_hash, job.profile = layout_hash, native_hash, preview.render_profile(scene)
job.same_session = lambda: True
job.guard()
cam.rotation_euler.z += .1
bpy.context.view_layer.update()
assert core.layout_hash(core.layout_dict(scene)) == layout_hash
try:
    job.guard()
except RuntimeError as exc:
    assert '原生相机' in str(exc)
else:
    raise AssertionError('native change during capture was accepted')
core.apply_parameters(cam, params)
bpy.context.view_layer.update()
job.guard()
checks['transaction_guard_detects_native_edit_with_unchanged_saved_layout'] = True

out = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-capture-native'))
out.mkdir(parents=True, exist_ok=True)
(out/'result.json').write_text(json.dumps({'status': 'PASS', 'blender': bpy.app.version_string,
    'module': addon.__file__, 'checks': checks}, indent=2))
addon.unregister()
print('CUSTOM_CAMERA_CAPTURE_NATIVE_PASS', len(checks), flush=True)
