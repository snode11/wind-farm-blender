"""Native controlled camera outputs bypass VSE/compositor and restore source settings."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import manual_down
from wfrl_blender.nrel_defects import editor, defect_outputs

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert bpy.app.background
addon.register()
addon.load_demo_scene()
bpy.ops.wfrl.farm_flex_view(turbine='all')
s = editor.ensure(bpy.context.scene)
source = s.scene
s.camera_resolution = lambda camera: (48, 32)
s.camera_signature = None
source.render.engine = 'BLENDER_EEVEE'
source.eevee.taa_render_samples = 1
source.render.resolution_x, source.render.resolution_y = 48, 32
source.render.resolution_percentage = 100
source.render.image_settings.file_format = 'PNG'
source.render.image_settings.color_mode = 'RGBA'
source.render.image_settings.color_depth = '8'
source.render.use_border = source.render.use_crop_to_border = False
source.render.use_motion_blur = False
source.view_settings.view_transform = 'Standard'
source.view_settings.look = 'None'
source.camera = s.cameras()[0]

def pixels(path):
    image = bpy.data.images.load(str(path), check_existing=False)
    values = np.array(image.pixels[:]).reshape(-1, 4)
    bpy.data.images.remove(image)
    return values


def render(scene, path):
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    return pixels(path)


def settings():
    r = source.render
    return (source.camera, r.filepath, r.engine, r.use_compositing, r.use_sequencer,
            r.image_settings.file_format, r.image_settings.color_mode, r.image_settings.color_depth,
            r.resolution_x, r.resolution_y, r.resolution_percentage, r.use_border,
            r.use_crop_to_border, r.use_motion_blur, source.frame_current, source.frame_subframe,
            source.compositing_node_group)

source.render.use_compositing = source.render.use_sequencer = False
raw = render(source, OUT / 'camera-source.png')
graph = bpy.data.node_groups.new('Controlled output compositor test', 'CompositorNodeTree')
graph.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
output = graph.nodes.new('NodeGroupOutput')
color = graph.nodes.new('CompositorNodeRGB')
color.outputs[0].default_value = (1, 0, 1, 1)
graph.links.new(color.outputs[0], output.inputs['Image'])
file_output = graph.nodes.new('CompositorNodeOutputFile')
file_output.directory = str(OUT / 'side-output')
file_output.file_output_items.new('RGBA', 'Camera')
layers = graph.nodes.new('CompositorNodeRLayers')
graph.links.new(layers.outputs['Image'], file_output.inputs[0])
source.compositing_node_group = graph
source.render.use_compositing = True
composited = render(source, OUT / 'compositor-control.png')
assert np.mean(composited[:, 0]) > .9 and np.mean(composited[:, 1]) < .1 and np.mean(composited[:, 2]) > .9
sequencer = source.sequence_editor_create()
strip = sequencer.strips.new_effect('VSE color override', 'COLOR', channel=1,
    frame_start=source.frame_current-1, length=3)
strip.color = (0, 1, 0)
source.render.use_compositing = False
source.render.use_sequencer = True
vse = render(source, OUT / 'vse-control.png')
assert np.mean(vse[:, 1]) > .9 and np.mean(vse[:, 0]) < .1 and np.mean(vse[:, 2]) < .1
source.render.use_compositing = True
before = settings()
side_files = {str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (OUT / 'side-output').rglob('*') if p.is_file()}
assert side_files
source_path = OUT / 'source.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source_path))
source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
checks = {}
for engine in ('SCENE', 'WORKBENCH'):
    work = source.copy()
    manual_down._configure_video(work, source.camera, 25, 1, engine, str(OUT / (engine + '.mp4')), size=(48, 32), samples=1)
    assert not work.render.use_compositing and not work.render.use_sequencer
    work.render.image_settings.media_type = 'IMAGE'
    work.render.image_settings.file_format = 'PNG'
    work.render.image_settings.color_mode = 'RGBA'
    work.render.image_settings.color_depth = '8'
    captured = render(work, OUT / (engine + '.png'))
    assert not np.allclose(captured, composited) and not np.allclose(captured, vse)
    if engine == 'SCENE':
        np.testing.assert_allclose(captured, raw, atol=1/255, rtol=0)
    bpy.data.scenes.remove(work)
    assert settings() == before
    checks['Down_' + engine] = 'camera pixels bypass VSE/compositor; source restored'
run = defect_outputs.capture_images(s, OUT / 'paired')
assert len(run['manifest']['images']) == 6
for image in run['manifest']['images']:
    values = pixels(run['output'] / image['path'])
    assert not np.allclose(values, composited) and not np.allclose(values, vse)
    if image['role'] == 'damaged' and image['camera_id'] == source.camera.name:
        np.testing.assert_allclose(values, raw, atol=1/255, rtol=0)
assert settings() == before
checks['NREL_paired_PNG'] = 'six native camera images; damaged C1 agrees with direct raw camera render'
real_bpy = defect_outputs.bpy
attempts = []
def fail_render(**kwargs):
    attempts.append(kwargs)
    raise RuntimeError('injected paired render failure')
defect_outputs.bpy = SimpleNamespace(app=real_bpy.app, ops=SimpleNamespace(render=SimpleNamespace(render=fail_render)))
try:
    try: defect_outputs.capture_images(s, OUT / 'paired-failure')
    except RuntimeError as exc: assert 'injected paired render failure' in str(exc)
    else: raise AssertionError('Expected controlled render failure')
finally:
    defect_outputs.bpy = real_bpy
assert attempts and settings() == before
assert s.geometry.active is s.payload
checks['NREL_failure_restoration'] = True
assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash
assert side_files == {str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (OUT / 'side-output').rglob('*') if p.is_file()}
checks['source_bytes_and_FileOutput_files_preserved'] = True
addon.unregister()
result = dict(status='PASS', module_file=addon.__file__, blender=bpy.app.version_string, checks=checks,
              scope='Native small PNG render pixels; no full video, daily installation or device capture')
(OUT / 'result.json').write_text(json.dumps(result, indent=2))
print('CAMERA_POSTPROCESSING_CONTRACT_PASS', json.dumps(result), flush=True)
