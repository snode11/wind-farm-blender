"""Native fixed-camera media, publication failure gates and excluded annotations."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import manual_down as down, custom_camera_preview as preview

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert bpy.app.background
addon.register()
try:
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    scene = bpy.context.scene
    intended = down.ensure_down_camera(scene, 'T1', reset=True)
    extra = bpy.data.objects.new('Contract alternate camera', bpy.data.cameras.new('Contract alternate optics'))
    scene.collection.objects.link(extra)
    extra.matrix_world = intended.matrix_world.copy()
    extra.data.lens = 50
    markers = [scene.timeline_markers.new('Camera '+str(frame), frame=frame) for frame in (1, 2)]
    for marker in markers:
        marker.camera = extra
    initial_camera = scene.camera

    def export(name):
        target = OUT/(name+'.mp4')
        down.export_down_video(scene, 'T1', str(target), fps=25, frames=2, engine='WORKBENCH',
                               blocking=True, size=(64, 64), samples=1, labels=False)
        data = json.loads(target.with_suffix('.json').read_text())
        assert data['optics']['lens_mm'] == 8 and data['verification']['full_decode']
        return target

    marked = export('markers')
    assert all(marker.camera == extra for marker in markers)
    assert scene.camera == initial_camera and extra.data.lens == 50
    for marker in markers:
        marker.camera = None
    plain = export('plain')
    ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
    pixels = [subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-f', 'rawvideo',
                             '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
              for path in (marked, plain)]
    assert pixels[0] == pixels[1]

    failed = []
    for bad_frame in (1, 2):
        active = [False]
        def render_pre(render_scene, *_):
            if render_scene.get('wfrl_manual_exporting'):
                active[0] = True
        def alter_lens(render_scene, *_):
            if active[0] and render_scene.get('wfrl_manual_exporting'):
                camera = next(obj for obj in down._down_cameras(render_scene) if obj['wfrl_turbine'] == 'T1')
                camera.data.lens = 50 if render_scene.frame_current == bad_frame else 8
        bpy.app.handlers.render_pre.append(render_pre)
        bpy.app.handlers.frame_change_post.append(alter_lens)
        target = OUT/f'rejected-{bad_frame}.mp4'
        try:
            try:
                down.export_down_video(scene, 'T1', str(target), fps=25, frames=2, engine='WORKBENCH',
                                       blocking=True, size=(64,64), samples=1, labels=False)
                raise AssertionError('Camera contract failure was published')
            except RuntimeError as exc:
                assert '光学参数' in str(exc), str(exc)
            assert not target.exists() and not target.with_suffix('.json').exists()
            state = down._LAST[scene.as_pointer()]
            assert state['state'] == 'FAILED' and Path(state['temporary']).is_dir()
            failed.append(dict(frame=bad_frame, state=state['state'], error=state['error']))
        finally:
            bpy.app.handlers.render_pre.remove(render_pre)
            bpy.app.handlers.frame_change_post.remove(alter_lens)

    layer = scene.view_layers[0]
    excluded = bpy.data.collections.new('Excluded contract annotations')
    scene.collection.children.link(excluded)
    hidden = bpy.data.objects.new('WFRL.Grid.ExcludedContract', None)
    excluded.objects.link(hidden)
    layer.layer_collection.children[excluded.name].exclude = True
    visible = bpy.data.objects.new('WFRL.Grid.VisibleContract', None)
    existing = bpy.data.objects.new('WFRL.Grid.HiddenContract', None)
    scene.collection.objects.link(visible)
    scene.collection.objects.link(existing)
    layer.update()
    existing.hide_set(True, view_layer=layer)
    for body_raises in (False, True):
        try:
            with preview.without_annotations(scene, layer):
                assert visible.hide_get(view_layer=layer) and existing.hide_get(view_layer=layer)
                assert hidden.name not in layer.objects
                if body_raises:
                    raise ValueError('controlled body failure')
        except ValueError as exc:
            assert body_raises and str(exc) == 'controlled body failure'
        assert not visible.hide_get(view_layer=layer) and existing.hide_get(view_layer=layer)
        assert layer.layer_collection.children[excluded.name].exclude
    result = dict(status='PASS', blender=bpy.app.version_string, module=down.__file__,
        marker_pixels_equal=True, decoded_sha256=hashlib.sha256(pixels[0]).hexdigest(),
        rejected_exports=failed, annotations_restored=True,
        source_sha256={name:hashlib.sha256((ROOT/'blender_frontend/wfrl_blender'/name).read_bytes()).hexdigest()
                       for name in ('manual_down.py','custom_camera_preview.py')})
    (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('CAMERA_EXPORT_CONTRACT_PASS', flush=True)
finally:
    addon.unregister()
