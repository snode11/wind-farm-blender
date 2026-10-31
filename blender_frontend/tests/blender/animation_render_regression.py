"""Independent animation rendering preserves source state and supports cancellation."""
import importlib
import json
import os
from pathlib import Path
import sys
import struct
import time

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
addon.register()
ui = importlib.import_module(MODULE + '.animation_render_ui')
service = importlib.import_module(MODULE + '.animation_render')
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)
scene = bpy.context.scene
graph = bpy.data.node_groups.new('Render Output Guard', 'CompositorNodeTree')
nested = bpy.data.node_groups.new('Nested Render Output Guard', 'CompositorNodeTree')
group = graph.nodes.new('CompositorNodeGroup')
group.node_tree = nested
file_output = nested.nodes.new('CompositorNodeOutputFile')
file_output.directory = str(output)
scene.compositing_node_group = graph
try:
    ui.start(bpy.context, output)
except ValueError as exc:
    assert '文件输出' in str(exc)
else:
    service.job.close()
    raise AssertionError('compositor file output bypassed preflight')
scene.compositing_node_group = None
bpy.data.node_groups.remove(graph)
bpy.data.node_groups.remove(nested)
scene.render.engine = 'BLENDER_EEVEE'
scene.render.resolution_x = scene.render.resolution_y = 32
scene.render.resolution_percentage = 100
scene.eevee.taa_render_samples = 1
scene.render.fps = 24
scene.view_settings.exposure = 1.25
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_depth = '32'
scene['wfrl_scene_kind'] = 'manual'
scene.frame_start, scene.frame_end, scene.frame_step = 2, 6, 2
scene.frame_set(4)
source = (bpy.data.filepath, scene.frame_current, scene.frame_subframe, scene.camera,
          scene.render.filepath, scene.render.resolution_x, scene.render.image_settings.file_format,
          scene.render.image_settings.color_depth)
directory = ui.start(bpy.context, output)
assert service.job.active
deadline = time.monotonic() + 90
while service.job.active and time.monotonic() < deadline:
    ui._poll()
    time.sleep(.1)
assert service.job.state == 'COMPLETE', (service.job.state, service.job.error, directory)
assert service.job.completed_frames == 3
assert source == (bpy.data.filepath, scene.frame_current, scene.frame_subframe, scene.camera,
                  scene.render.filepath, scene.render.resolution_x, scene.render.image_settings.file_format,
                  scene.render.image_settings.color_depth)
manifest = json.loads((directory / 'manifest.json').read_text())
assert len(manifest['frames']) == 3
assert [row['frame'] for row in manifest['frames']] == [2, 4, 6]
for row in manifest['frames']:
    pixels = (directory / row['file']).read_bytes()
    assert pixels.startswith(b'\x89PNG\r\n\x1a\n')
    assert pixels[24] == 16

# A stored job may retain a higher-precision source depth at the worker boundary.
from unittest.mock import patch
settings = ui._render_settings(scene)
settings['image_settings']['color_depth'] = '32'
scene.frame_start = scene.frame_end = 4
with patch.object(ui, '_render_settings', return_value=settings):
    legacy_depth = ui.start(bpy.context, output)
deadline = time.monotonic() + 90
while service.job.active and time.monotonic() < deadline:
    ui._poll()
    time.sleep(.1)
assert service.job.state == 'COMPLETE', (service.job.state, service.job.error)
legacy_manifest = json.loads((legacy_depth / 'manifest.json').read_text())
assert (legacy_depth / legacy_manifest['frames'][0]['file']).read_bytes()[24] == 16
scene.pop('wfrl_scene_kind')

scene.frame_start, scene.frame_end, scene.frame_step = 1, 500, 1
cancelled = ui.start(bpy.context, output)
started = time.monotonic()
service.job.cancel()
assert time.monotonic() - started < .2
deadline = time.monotonic() + 10
while service.job.active and time.monotonic() < deadline:
    ui._poll()
    time.sleep(.1)
assert service.job.state == 'CANCELLED', service.job.error
assert json.loads((cancelled / 'manifest.json').read_text())['state'] == 'CANCELLED'

addon.load_demo_scene(camera_rig=False)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 96, 54
scene.render.resolution_percentage = 100
scene.eevee.taa_render_samples = 1
scene.render.fps = 24
scene.view_settings.exposure = 1.25
scene.frame_start, scene.frame_end, scene.frame_step = 1, 601, 300
scene.frame_set(13, subframe=.25)
before = (scene.frame_current, scene.frame_subframe, scene.camera, scene.render.filepath)
mappo = ui.start(bpy.context, output)
deadline = time.monotonic() + 120
while service.job.active and time.monotonic() < deadline:
    ui._poll()
    time.sleep(.1)
assert service.job.state == 'COMPLETE', (service.job.state, service.job.error, mappo)
assert before == (scene.frame_current, scene.frame_subframe, scene.camera, scene.render.filepath)
motion = json.loads((mappo / 'manifest.json').read_text())
assert [row['frame'] for row in motion['frames']] == [1, 301, 601]
assert motion['fps'] == 24
assert motion['exposure'] == 1.25
images = [(mappo / row['file']).read_bytes() for row in motion['frames']]
assert all(struct.unpack('>II', image[16:24]) == (96, 54) for image in images)
assert len(set(images)) == 3, 'recorded motion did not produce distinct frame images'

native = importlib.import_module(MODULE + '.native_camera_views')
session = native.NativeSession.__new__(native.NativeSession)
session.scene = scene
session.preview_quality = ('1', 64, True, True)
session.set_quality(('2', 8, False, False))
native._ACTIVE = session
scene.frame_start = scene.frame_end = 1
try:
    quality_output = ui.start(bpy.context, output)
    assert session.quality_settings() == ('2', 8, False, False)
    frozen = json.loads((quality_output / 'job.json').read_text())
    assert frozen['render_settings']['eevee']['use_shadows'] is True
    assert frozen['render_settings']['eevee']['use_fast_gi'] is True
    assert frozen['render_settings']['eevee']['taa_samples'] == 64
finally:
    service.job.close()
    native._ACTIVE = None
    session.set_quality(session.preview_quality)
result = dict(status='PASS', blender=bpy.app.version_string, module=addon.__file__,
              complete_frames=3, source_preserved=True, cancellation=True,
              output=str(directory), cancelled=str(cancelled), mappo_output=str(mappo),
              recorded_motion_frames=[1, 301, 601], compositor_output_guard=True,
              original_camera_quality_snapshot=True)
(output / 'result.json').write_text(json.dumps(result, indent=2))
print('ANIMATION_RENDER_REGRESSION_PASS', json.dumps(result), flush=True)
addon.unregister()
