"""Actual-window operator/modal regression; keeps the review window open.

Uses a new WFRL_TEST_OUTPUT directory and optionally WFRL_ADDON_MODULE for an
isolated installed extension. No image rendering or backend computation.
"""
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
editor = importlib.import_module(MODULE+'.nrel_defects.editor')
schema = importlib.import_module(MODULE+'.nrel_defects.defect_schema')
outputs = importlib.import_module(MODULE+'.nrel_defects.defect_outputs')
out = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not bpy.app.background, 'This regression requires an actual window'
assert not out.exists() or not any(out.iterdir()), 'Use a new evidence directory'
out.mkdir(parents=True, exist_ok=True)
addon.register()
addon.load_demo_scene()
s = editor.ensure(bpy.context.scene)
scene = s.scene
p = scene.wfrl_nrel_defects
doc = s.store.document
doc['defects'] = [schema.new_defect(s.surface, kind) for kind in schema.PRESETS]
s.commit_document(doc)
p.selected = 2
committed = s.store.document
assert bpy.ops.wfrl.nrel_defect_action(action='EDIT') == {'FINISHED'}
assert s.draft['defects'][s.draft_index]['morphology'] == 'pit'
p.width *= 1.1
assert bpy.ops.wfrl.nrel_defect_action(action='PREVIEW') == {'FINISHED'}
assert s.store.document == committed
assert bpy.ops.wfrl.nrel_defect_action(action='CANCEL') == {'FINISHED'}
assert s.draft is None and s.store.document == committed

# Exercise failed A capture restoration without invoking Blender's renderer.
# Only this output module's bpy binding is replaced; other modules use real bpy.
def render_failure(**kwargs):
    raise RuntimeError('Injected render failure; no renderer called')

r = scene.render
saved = (scene.camera, r.filepath, r.resolution_x, r.resolution_y,
         r.resolution_percentage, r.image_settings.file_format,
         r.image_settings.color_mode, r.image_settings.color_depth,
         r.use_file_extension, float(scene['defect_time_s']))
real_bpy = outputs.bpy
outputs.bpy = SimpleNamespace(app=bpy.app,
    ops=SimpleNamespace(render=SimpleNamespace(render=render_failure)))
try:
    try:
        outputs.capture_images(s, out/'a-failure')
    except RuntimeError as exc:
        assert str(exc).startswith('Injected render failure')
    else:
        raise AssertionError('Injected renderer must fail')
finally:
    outputs.bpy = real_bpy
current = (scene.camera, r.filepath, r.resolution_x, r.resolution_y,
           r.resolution_percentage, r.image_settings.file_format,
           r.image_settings.color_mode, r.image_settings.color_depth,
           r.use_file_extension, float(scene['defect_time_s']))
assert current == saved
assert s.geometry.active is s.payload and s.store.document == committed
assert json.loads((out/'a-failure/run_manifest.json').read_text())['state'] == 'A_FAILED'
assert not list(out.rglob('*.png'))

p.scan_start = float(s.flex.times[0])
p.scan_duration = 1/60
p.output_path = str(out/'scan')
assert bpy.ops.wfrl.nrel_defect_scan('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
assert s.busy

def finish():
    if s.busy:
        return .5
    try:
        manifest = json.loads((out/'scan/run_manifest.json').read_text())
        rows = json.loads((out/'scan/visibility.json').read_text())
        assert manifest['state'] == 'G_SCAN_COMPLETE', manifest['state']
        assert manifest['products']['visible_intervals']['complete']
        assert len(rows) == 36 and all(row['status'] != 'INVALID' for row in rows)
        assert float(scene['defect_time_s']) == saved[-1]
        assert s.store.document == committed and not list(out.rglob('*.png'))
        s.closeup(2, True)
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.show_region_ui = True
                    area.spaces.active.overlay.show_overlays = False
                    area.spaces.active.shading.type = 'MATERIAL'
        result = dict(status='PASS', module_file=addon.__file__,
            operator_edit_preview_cancel=True, modal_scan_records=len(rows),
            render_failure_restoration=True, images_rendered=0,
            scope='Actual-window operators and modal lifecycle; no A image acceptance or performance measurement')
        (out/'window-checks.json').write_text(json.dumps(result, indent=2))
        print('NREL_DEFECT_WINDOW_PASS', json.dumps(result), flush=True)
    except BaseException as exc:
        (out/'window-checks.json').write_text(json.dumps(dict(status='FAIL', error=repr(exc)), indent=2))
        raise
    return None

bpy.app.timers.register(finish, first_interval=.5)
