"""Down export renders its own camera despite camera-bound markers; annotation exclusion restores."""
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
    # Export camera shake scales with RPM and takes a random phase per export; at 0 RPM
    # without structural motion two exports of the same pose are pixel-identical.
    for entry in scene.wfrl_manual_turbines:
        entry.rpm = 0.0
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
                               blocking=True, size=(64, 64), samples=1, labels=False, dynamics=False)
        data = json.loads(target.with_suffix('.json').read_text(encoding='utf-8'))
        assert data['optics']['lens_mm'] == 8, data['optics']
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
    assert len(pixels[0]) == 2 * 64 * 64 * 3 and pixels[0] == pixels[1]

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
        annotations_restored=True,
        source_sha256={name:hashlib.sha256((ROOT/'blender_frontend/wfrl_blender'/name).read_bytes()).hexdigest()
                       for name in ('manual_down.py','custom_camera_preview.py')})
    (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('CAMERA_EXPORT_CONTRACT_PASS', flush=True)
finally:
    addon.unregister()
