"""Short source-only window probe of idle redraws and injected input events.

Run with --factory-startup --enable-event-simulate and a unique WFRL_TEST_OUTPUT.
WFRL_REDRAW_BASELINE optionally supplies modal/show_camera bodies for a matched
idle comparison in the same process, scene and Preview implementation. Counts
are redraw requests and callbacks, not FPS or physical input-to-display latency.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import custom_cameras as core, custom_camera_preview as preview, runtime
from wfrl_blender.panels import custom_cameras as panel

assert not bpy.app.background
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
STATE = dict(step='setup', redraws=0, polls=0, timers=0, own_advances=0)
RESULT = dict(status='RUNNING', blender=bpy.app.version_string, source=addon.__file__,
              counts={}, checks={}, input='Window.event_simulate; no OS mouse latency claim',
              event_has_timer='timer' in bpy.types.Event.bl_rna.properties,
              panel_sha256=hashlib.sha256(Path(panel.__file__).read_bytes()).hexdigest())
CURRENT = Path(panel.__file__).read_text()
BASELINE_PATH = os.environ.get('WFRL_REDRAW_BASELINE')
BASELINE = Path(BASELINE_PATH).read_text() if BASELINE_PATH else None
ORIGINAL_MODAL = panel.WFRL_OT_CustomCamera.modal
ORIGINAL_SHOW = panel.WFRL_OT_CustomCamera.show_camera
ORIGINAL_REFRESH = preview.Preview.refresh
ORIGINAL_REDRAW = panel._redraw_if_changed
ORIGINAL_TIMER = panel._timer_advanced


def counted_redraw(area):
    STATE['redraws'] += 1
    area.tag_redraw()


def redraw(op):
    changed = ORIGINAL_REDRAW(op)
    STATE['redraws'] += int(changed)
    return changed


def refresh(self, *args, **kwargs):
    STATE['polls'] += 1
    return ORIGINAL_REFRESH(self, *args, **kwargs)


def timer_advanced(op, event):
    advanced = ORIGINAL_TIMER(op, event)
    STATE['own_advances'] += int(advanced)
    return advanced


class TraceRedraw(ast.NodeTransformer):
    def visit_Call(self, node):
        node = self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'tag_redraw':
            return ast.copy_location(ast.Call(func=ast.Name(id='_probe_redraw', ctx=ast.Load()),
                                              args=[node.func.value], keywords=[]), node)
        return node


def instrument(source):
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == 'WFRL_OT_CustomCamera')
    body = [n for n in cls.body if isinstance(n, ast.FunctionDef)
            and n.name in {'modal', 'show_camera'}]
    tree = ast.fix_missing_locations(TraceRedraw().visit(ast.Module(body=body, type_ignores=[])))
    namespace = dict(panel.__dict__, _probe_redraw=counted_redraw)
    exec(compile(tree, panel.__file__, 'exec'), namespace)
    modal = namespace['modal']

    def counted_modal(self, context, event):
        if event.type == 'TIMER':
            STATE['timers'] += 1
        return modal(self, context, event)

    panel.WFRL_OT_CustomCamera.modal = counted_modal
    panel.WFRL_OT_CustomCamera.show_camera = namespace['show_camera']


def measure(label):
    STATE.update(step='measure', label=label, started=time.monotonic(),
                 redraws=0, polls=0, timers=0, own_advances=0)


def send(kind, value, x, y):
    region = STATE['region']
    STATE['window'].event_simulate(type=kind, value=value,
                                    x=int(region.x + x), y=int(region.y + y))


def finish(error=None):
    RESULT['status'] = 'FAIL' if error else 'PASS'
    if error:
        RESULT['error'] = error
    (OUT / 'result.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2))
    print('CUSTOM_CAMERA_REDRAW_' + RESULT['status'], flush=True)
    if error:
        print(error, flush=True)
    panel.WFRL_OT_CustomCamera.modal = ORIGINAL_MODAL
    panel.WFRL_OT_CustomCamera.show_camera = ORIGINAL_SHOW
    preview.Preview.refresh = ORIGINAL_REFRESH
    panel._redraw_if_changed = ORIGINAL_REDRAW
    panel._timer_advanced = ORIGINAL_TIMER
    if STATE.get('foreign_timer'):
        bpy.context.window_manager.event_timer_remove(STATE['foreign_timer'])
    if panel._ACTIVE:
        panel._ACTIVE.finish()
    addon.unregister()
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if STATE['step'] == 'setup':
            addon.register()
            runtime.enter_local_demo()
            scene = bpy.context.scene
            root = bpy.data.objects.new(core.ROOT_NAME, None)
            scene.collection.objects.link(root)
            shell = scene.objects['Cube']
            shell.name = core.SURFACE_NAMES[0]
            shell.parent = root
            camera = core.begin_draft(scene, 1)
            core.apply_parameters(camera, core.CameraParameters((1.1, 0, 0), yaw=180),
                                  core.SurfaceAnchor((1, 0, 0), (1, 0, 0), .1))
            core.commit_draft(scene, 1, camera)
            window = bpy.context.window
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            area.spaces.active.show_region_ui = False
            region = next(r for r in area.regions if r.type == 'WINDOW')
            STATE.update(window=window, area=area, region=region)
            RESULT['scene'] = 'factory cube as nacelle shell; one fixed camera; no replay assets'
            panel._redraw_if_changed = redraw
            panel._timer_advanced = timer_advanced
            preview.Preview.refresh = refresh
            instrument(CURRENT)
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT', mode='WATCH') == {'RUNNING_MODAL'}
            STATE['foreign_timer'] = bpy.context.window_manager.event_timer_add(.004, window=window)
            STATE.update(step='first_image', deadline=time.monotonic() + 12)
            return .1
        with bpy.context.temp_override(window=STATE['window'], area=STATE['area'], region=STATE['region']):
            op = panel._ACTIVE
            assert op is not None and not op.closed
            step = STATE['step']
            if step == 'first_image':
                if not op.preview.images:
                    assert time.monotonic() < STATE['deadline'], op.preview.error
                    return .1
                RESULT['checks']['first_image'] = True
                instrument(BASELINE or CURRENT)
                STATE.update(step='settle', next_label='baseline' if BASELINE else 'watch')
                return .5
            if step == 'settle':
                measure(STATE['next_label'])
                return .1
            if step == 'measure':
                elapsed = time.monotonic() - STATE['started']
                if elapsed < 1.:
                    return .1
                label = STATE['label']
                RESULT['counts'][label] = dict(elapsed_s=elapsed,
                    **{key: STATE[key] for key in ('redraws', 'polls', 'timers', 'own_advances')})
                if label == 'baseline':
                    instrument(CURRENT)
                    STATE.update(step='settle', next_label='watch')
                    return .3
                assert STATE['redraws'] == 0, RESULT['counts'][label]
                assert 0 < STATE['polls'] == STATE['own_advances'] < STATE['timers']
                if label == 'watch':
                    assert bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT', mode='EDIT') == {'RUNNING_MODAL'}
                    STATE.update(step='settle', next_label='aim')
                    return 1.
                STATE['start_params'] = core.parameters(op.camera)
                STATE['start_version'] = op.preview.version
                STATE['redraws'] = 0
                bpy.context.window_manager.wfrl_custom_fov = STATE['start_params'].fov + 2
                assert STATE['redraws'] > 0
                STATE.update(step='parameter_image', deadline=time.monotonic() + 5)
                return .1
            if step == 'parameter_image':
                if op.preview.version <= STATE['start_version']:
                    assert time.monotonic() < STATE['deadline'], op.preview.status()
                    return .1
                RESULT['checks']['numeric_callback_and_new_image'] = True
                cx, cy, radius = op.geometry()
                STATE['before_stick'] = core.parameters(op.camera)
                send('MOUSEMOVE', 'NOTHING', cx + radius * .5, cy)
                send('LEFTMOUSE', 'PRESS', cx + radius * .5, cy)
                STATE['step'] = 'stick'
                return .2
            if step == 'stick':
                assert op.drag == 'stick'
                assert core.parameters(op.camera).yaw != STATE['before_stick'].yaw
                cx, cy, _ = op.geometry()
                send('LEFTMOUSE', 'RELEASE', cx, cy)
                STATE['step'] = 'release'
                return .1
            if step == 'release':
                assert op.drag is None and op.stick == (0, 0)
                RESULT['checks']['injected_stick_integrates_on_real_timer_and_releases'] = True
                STATE['before_drag'] = core.parameters(op.camera)
                send('MOUSEMOVE', 'NOTHING', 120, 180)
                send('LEFTMOUSE', 'PRESS', 120, 180)
                send('MOUSEMOVE', 'NOTHING', 170, 200)
                send('LEFTMOUSE', 'RELEASE', 170, 200)
                STATE['step'] = 'drag'
                return .1
            if step == 'drag':
                assert op.drag is None
                assert core.parameters(op.camera).yaw != STATE['before_drag'].yaw
                RESULT['checks']['injected_look_drag'] = True
                assert bpy.ops.wfrl.custom_camera_action(action='CANCEL') == {'FINISHED'}
                assert panel._ACTIVE.stage == 'WATCH'
                assert core.parameters(panel._ACTIVE.camera) == STATE['start_params']
                RESULT['checks']['cancel_restores_parameters'] = True
                send('ESC', 'PRESS', 120, 180)
                STATE['step'] = 'closed'
                return .1
    except Exception:
        finish(traceback.format_exc())
        return None
    return .1


def dispatch():
    if STATE['step'] == 'closed':
        try:
            assert panel._ACTIVE is None
            RESULT['checks']['injected_escape_closes'] = True
            finish()
        except Exception:
            finish(traceback.format_exc())
        return None
    return tick()


bpy.app.timers.register(dispatch, first_interval=.5)
