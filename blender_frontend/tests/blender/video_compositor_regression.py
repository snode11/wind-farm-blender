"""Isolated video render settings bypass compositor pixels and side outputs."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import camera_video, cameras, manual_video_render

assert bpy.app.background
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
addon.register()
addon.load_demo_scene(camera_rig=False)
source = bpy.context.scene
source.camera = cameras.ensure_nacelle_gimbal(source, 'T1')
source.render.engine = 'BLENDER_EEVEE'
source.eevee.taa_render_samples = 1
source.render.resolution_x = source.render.resolution_y = 48
source.render.resolution_percentage = 100
source.render.image_settings.file_format = 'PNG'
source.view_settings.view_transform = 'Standard'
source.view_settings.look = 'None'
source.view_settings.exposure = 0
source.view_settings.gamma = 1
graph = bpy.data.node_groups.new('Video compositor isolation', 'CompositorNodeTree')
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
source_path = OUT / 'source.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source_path))
source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

def render(scene, path, frame):
    scene.frame_set(frame)
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    image = bpy.data.images.load(str(path), check_existing=False)
    pixel = tuple(image.pixels[:4])
    bpy.data.images.remove(image)
    return pixel

control = render(source, OUT / 'composited.png', 1)
side_files = {str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (OUT / 'side-output').rglob('*') if p.is_file()}
assert side_files, 'Control render did not exercise File Output'
assert control[0] > .9 and control[1] < .1 and control[2] > .9, control
settings = source.render.use_compositing, source.compositing_node_group, source.render.engine
work = source.copy()
manual_video_render._configure_render(work, source.camera,
                                      dict(width=48, height=48, fps=25, samples=1))
assert not work.render.use_compositing
for frame in (2, 3):
    pixel = render(work, OUT / f'manual-{frame}.png', frame)
    assert max(abs(a-b) for a, b in zip(pixel[:3], control[:3])) > .1, pixel
assert settings == (source.render.use_compositing, source.compositing_node_group, source.render.engine)
assert side_files == {str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (OUT / 'side-output').rglob('*') if p.is_file()}
addon.unregister()

parser = runpy.run_path(str(ROOT / 'scripts/blender/render_camera_video.py'))['parser']
args = parser().parse_args(['--scene', str(source_path), '--output', str(OUT / 'masters'),
                           '--engine', 'eevee', '--width', '48', '--height', '48', '--samples', '1',
                           '--test-resolution'])
worker, camera, active, hidden = camera_video.prepare_scene(args)
assert not worker.render.use_compositing
worker.render.image_settings.file_format = 'PNG'
worker.render.image_settings.color_mode = 'RGBA'
worker.render.image_settings.color_depth = '8'
for frame in (4, 5):
    pixel = render(worker, OUT / f'camera-{frame}.png', frame)
    assert max(abs(a-b) for a, b in zip(pixel[:3], control[:3])) > .1, pixel
assert source_hash == hashlib.sha256(source_path.read_bytes()).hexdigest()
assert side_files == {str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (OUT / 'side-output').rglob('*') if p.is_file()}
addon.unregister()
result = dict(status='PASS', final=True, blender=bpy.app.version_string, addon_file=addon.__file__,
              manual_frames=2, camera_frames=2, control_file_output_observed=True,
              extra_worker_outputs=False, source_bytes_preserved=True, source_settings_preserved=True,
              scope='Small native renders; complete video encoding and GPU throughput not measured')
(OUT / 'result.json').write_text(json.dumps(result, indent=2))
print('VIDEO_COMPOSITOR_PASS', json.dumps(result), flush=True)
