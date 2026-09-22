"""Native background contract: source clock, fixed mount, clean rays, resume.

Run with WFRL_TEST_OUTPUT=/tmp/... Blender --background --factory-startup
--python-exit-code 1 --python blender_frontend/tests/blender/camera_video_regression.py
"""
from pathlib import Path
import json
import os
import runpy
import sys
import tempfile
import numpy as np
import bpy
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
from wfrl_blender import camera_video as video
from wfrl.camera_video.data import SourceGeometry, frame_schedule
parser = runpy.run_path(str(ROOT / 'scripts/blender/render_camera_video.py'))['parser']
output = Path(os.environ.get('WFRL_TEST_OUTPUT', tempfile.mkdtemp(prefix='wfrl-camera-regression-')))
output.mkdir(parents=True, exist_ok=True)
args = parser().parse_args(['--output', str(output / 'masters'), '--start-s', '117.7',
    '--end-s', '117.8', '--width', '160', '--height', '90', '--samples', '2',
    '--exposure-samples', '2', '--test-resolution', '--max-frames', '1'])
args.exposure_s = .025
# A saved scene may carry lossy EXR and per-format color overrides.
bpy.context.scene.render.image_settings.exr_codec = 'DWAA'
bpy.context.scene.render.image_settings.color_management = 'OVERRIDE'
scene, camera, active, hidden = video.prepare_scene(args)
assert active.export_turbines == {0}
assert len(active.blades) == 3 and len(active.towers) == 1
assert all(record[1] == 0 for record in active.fittings)
assert not any({'T2', 'T3'} & set(obj.name.split('.')) for obj in scene.objects)
assert not any(video.is_auxiliary(obj) for obj in scene.objects)
assert scene.render.image_settings.exr_codec == 'ZIP'
assert scene.render.image_settings.color_management == 'FOLLOW_SCENE'
source = SourceGeometry(args.package)
config = video.camera_configuration(scene, camera, args)
mount = np.asarray(config['camera_mount'])
local = np.asarray(camera.matrix_basis).copy()
errors = []
positions = []
for t in (117., 117.012345, 117.025, 132.017, 176.998, 177.):
    active.update(scene, sim_time_s=t, record_telemetry=False)
    bpy.context.view_layer.update()
    expected = source.sample(t)['nacelle_world_transform'] @ mount
    errors.append(float(np.max(np.abs(np.asarray(camera.matrix_world) - expected))))
    np.testing.assert_allclose(camera.matrix_basis, local, atol=1e-7, rtol=0)
    assert abs(scene['wfrl_clearance_time_s'] - t) < 1e-10
    positions.append(np.asarray(active.blades[0][0].matrix_world @ active.blades[0][0].data.vertices[-1].co).copy())
    video.clean_scene(scene)
    assert all(obj.hide_render for obj in scene.objects if video.is_auxiliary(obj))
assert max(errors) < 2e-4, errors
assert np.linalg.norm(positions[0]-positions[1]) > .01
for fps in (15, 20, 25):
    centers = video.frame_times(117., 177., fps, .5 / fps)
    rows = frame_schedule(117., 177., fps, .5 / fps)
    np.testing.assert_array_equal(centers, [row['sim_time_s'] for row in rows])
    assert len(centers) == 60 * fps
    assert min(video.shutter_times(centers[0], .5/fps, 8)) >= 117.
    assert max(video.shutter_times(centers[-1], .5/fps, 8)) <= 177.
# Validate that a scene-linear .5 integral receives exactly one sRGB transform.
# A pre-transformed/averaged or twice-transformed value fails this pixel check.
import struct
import zlib
saved_color = {k: getattr(scene.view_settings, k) for k in ('view_transform','look','exposure','gamma')}
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'
scene.view_settings.exposure = 0
scene.view_settings.gamma = 1
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.image_settings.color_depth = '16'
probe = bpy.data.images.new('LinearExposureProbe', width=1, height=1, alpha=True, float_buffer=True)
probe.colorspace_settings.name = 'Linear Rec.709'
probe.pixels[:] = [.5, .5, .5, 1]
probe.save_render(str(output / 'linear-transform.png'), scene=scene)
bpy.data.images.remove(probe)
payload = (output / 'linear-transform.png').read_bytes()
position = 8
compressed = b''
while position < len(payload):
    length = struct.unpack('>I', payload[position:position+4])[0]
    kind = payload[position+4:position+8]
    if kind == b'IDAT': compressed += payload[position+8:position+8+length]
    position += 12 + length
raw = zlib.decompress(compressed)
# First pixel in first row has zero neighbours for every PNG predictor.
encoded = struct.unpack('>H', raw[1:3])[0] / 65535
expected = 1.055 * .5 ** (1/2.4) - .055
assert abs(encoded - expected) < 2e-4, (encoded, expected)
for key, value in saved_color.items(): setattr(scene.view_settings, key, value)
# Same-process independent file descriptors also contend on the advisory lock.
with video.output_lock(output / 'lock-test', args.package):
    try:
        with video.output_lock(output / 'lock-test', args.package):
            raise AssertionError('Concurrent lock unexpectedly acquired')
    except RuntimeError as error:
        assert 'Another render process owns' in str(error)
with video.output_lock(output / 'lock-test', args.package): pass
# Render, resume, reject configuration mixing, and reject corrupted masters.
first = video.render_package(args)
assert first['status'] == 'partial' and first['rendered_this_run'] == 1
args.max_frames = None
second = video.render_package(args)
assert second['status'] == 'complete' and second['rendered_this_run'] == 1
third = video.render_package(args)
assert third['rendered_this_run'] == 0
args.samples += 1
try:
    video.render_package(args)
except ValueError as error:
    assert 'configuration mismatch' in str(error)
else:
    raise AssertionError('Mixed render settings were accepted')
args.samples -= 1
frame = output / 'masters/master_frames/000000.png'
original = frame.read_bytes()
frame.write_bytes(original + b'corruption')
try:
    video.render_package(args)
except ValueError as error:
    assert 'Changed master frame' in str(error)
else:
    raise AssertionError('Corrupted master was accepted')
finally:
    frame.write_bytes(original)
# Interrupt precisely at the PNG commit boundary. Metadata must already name
# the verified partial image, and the next run must recover without manual edits.
from copy import copy
from unittest.mock import patch
crash_args = copy(args)
crash_args.output = str(output / 'interrupted-commit')
crash_args.end_s = crash_args.start_s + 1 / crash_args.fps
original_replace = Path.replace

def interrupt_png_commit(path, target):
    if path.name == '000000.partial.png':
        raise RuntimeError('simulated interruption before PNG commit')
    return original_replace(path, target)

with patch.object(Path, 'replace', interrupt_png_commit):
    try:
        video.render_package(crash_args)
    except RuntimeError as error:
        assert 'simulated interruption before PNG commit' in str(error)
    else:
        raise AssertionError('PNG commit interruption was not exercised')
crash_root = Path(crash_args.output)
crash_records = json.loads((crash_root / 'checks/render_frames.json').read_text())
partial = crash_root / 'master_frames/000000.partial.png'
assert crash_records['0']['sha256'] == video.sha256(partial)
assert not (crash_root / 'master_frames/000000.png').exists()
recovered = video.render_package(crash_args)
assert recovered['status'] == 'complete' and recovered['rendered_this_run'] == 1
assert not partial.exists()
result = dict(status='PASS', blender=bpy.app.version_string, arbitrary_time=True,
    fixed_mount_max_matrix_error=max(errors), helper_objects_hidden_from_all_render_rays=len(hidden),
    single_turbine_scene=True, other_turbine_geometry_removed=True,
    shared_frame_schedule=True, exposure_subsamples=True, linear_color_transform_error=abs(encoded-expected), render_resume=True,
    config_mismatch_rejected=True, corruption_rejected=True, output_lock=True, interrupted_commit_recovered=True, lossless_exr_zip=True, image_color_management_follows_scene=True,
    limitation='small diagnostic renders; no production quality convergence claim')
video.write_json(output / 'checks.json', result)
print('CAMERA_VIDEO_REGRESSION', json.dumps(result), flush=True)
