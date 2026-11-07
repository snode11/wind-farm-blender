"""Background edit transactions preserve native camera and exit bindings.

Uses real EditSession, ViewportSnapshot and controller methods with an AIM
controller initialized without modal invocation. No GPU or mouse-input claim.
Run in a separate factory-startup process with a new WFRL_TEST_OUTPUT directory.
"""
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
from types import MethodType, SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[3]
assert bpy.app.background, 'This regression requires --background'
assert os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender') == 'wfrl_blender'
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
core = importlib.import_module('wfrl_blender.custom_cameras')
history = importlib.import_module('wfrl_blender.custom_camera_history')
panel = importlib.import_module('wfrl_blender.panels.custom_cameras')

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)
REPORT = {
    'status': 'RUNNING',
    'addon_module': addon.__name__,
    'addon_file': addon.__file__,
    'blender': bpy.app.version_string,
    'background': bpy.app.background,
    'entry': 'Real edit session and controller methods; manually initialized AIM controller',
    'source_sha256': {
        module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        for module in (core, panel)
    },
    'checks': {},
    'failures': {},
}
CASES = (
    ('overview_global', 'overview', False, 'overview'),
    ('C2_global', 'C2', False, 'C2'),
    ('C2_scene_and_local', 'C2', True, 'C2'),
    ('overview_scene_C2_local', 'overview', True, 'C2'),
    ('C2_scene_overview_local', 'C2', True, 'overview'),
)


def save_report():
    (OUT / 'checks.json').write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=2), encoding='utf-8')


def reference(scene, camera):
    if camera is None:
        return None
    try:
        return dict(name=camera.name, pointer=camera.as_pointer(),
                    live=scene.objects.get(camera.name) == camera)
    except ReferenceError:
        return dict(live=False, error='removed RNA')


def snapshot_refs(scene, op):
    return {
        'snapshot': reference(scene, op.snapshot.camera),
        'external_snapshot': reference(scene, op.external_snapshot.camera),
        'session_snapshot': reference(scene, op.session.snapshot.camera),
    }


def state(scene, space, op):
    return {
        'scene_camera': reference(scene, scene.camera),
        'viewport_camera': reference(scene, space.camera),
        'use_local_camera': space.use_local_camera,
        'current_C2': reference(scene, core.get_camera(scene, 2)),
        'stage': op.stage if op else None,
        'snapshots': snapshot_refs(scene, op) if op else None,
    }


def assert_camera(scene, actual, expected, label):
    actual_ref = reference(scene, actual)
    assert actual_ref and actual_ref['live'] and actual == expected, (
        label, actual_ref, reference(scene, expected))


def cleanup_controller():
    if panel._ACTIVE:
        panel._ACTIVE.finish(restore=False, resume=False)


def make_controller(scene, window, area, wm, return_stage=None):
    op = SimpleNamespace(
        scene=scene, slot=2, area=area, window=window, wm=wm,
        stage='AIM', ready=True, closed=False, error='', drag=None,
        return_stage=return_stage, timer=None, handle=None,
    )
    for name in ('show_camera', 'select_slot', 'return_to_view', 'external', 'finish', 'cancel_edit'):
        setattr(op, name, MethodType(getattr(panel.WFRL_OT_CustomCamera, name), op))
    op.snapshot = panel.ViewportSnapshot(area)
    # A manually initialized controller can inherit an independent exit snapshot
    # from WATCH/LAYOUT without invoking a modal operator in background mode.
    op.external_snapshot = panel.ViewportSnapshot(area) if return_stage else op.snapshot
    op.session = panel.EditSession(scene, 2, area, window, wm)
    op.camera = op.session.begin()
    op.preview = panel.preview.Preview()
    panel._ACTIVE = op
    return op


def assert_bindings(scene, space, case, overview, c2):
    _, scene_kind, local, viewport_kind = case
    assert space.use_local_camera == local
    assert_camera(scene, scene.camera, c2 if scene_kind == 'C2' else overview, 'scene.camera')
    assert_camera(scene, space.camera, c2 if viewport_kind == 'C2' else overview, 'viewport camera')


def assert_snapshots(scene, op, expected):
    for name, snapshot in (
        ('snapshot', op.snapshot), ('external_snapshot', op.external_snapshot),
        ('session.snapshot', op.session.snapshot),
    ):
        assert_camera(scene, snapshot.camera, expected, name)


def run_case(scene, window, area, region, overview, baseline, case, operation, return_stage=None):
    name = operation + '/' + case[0]
    if return_stage:
        name += '/from_' + return_stage
    rows = {}
    checkpoint = core._publication_checkpoint
    space = area.spaces.active
    try:
        cleanup_controller()
        core.restore_layout(scene, baseline)
        history.HISTORY.clear()
        c2 = core.get_camera(scene, 2)
        original_ids = {slot: core.get_camera(scene, slot) for slot in core.SLOTS}
        _, scene_kind, local, viewport_kind = case
        scene.camera = c2 if scene_kind == 'C2' else overview
        space.use_local_camera = local
        space.camera = c2 if viewport_kind == 'C2' else overview
        window.scene = scene
        wm = bpy.context.window_manager
        wm.wfrl_custom_slot = 2
        before = core.live_layout_hash(scene)
        before_parameters = {slot: core.parameters(camera) for slot, camera in original_ids.items()}
        history_count = len(history.HISTORY.entries)
        rows['before'] = state(scene, space, None)
        with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
            op = make_controller(scene, window, area, wm, return_stage=return_stage)
            if return_stage:
                assert op.external_snapshot is not op.snapshot
                rows['inherited_exit_snapshot_independent'] = True
                rows['return_stage'] = return_stage
            target = before_parameters[2]
            if operation != 'confirm_unchanged':
                target = replace(target, pitch=target.pitch + 1., fov=target.fov + 2.,
                                 output_long_edge_px=1280)
                core.apply_research(scene, op.camera, target, confirmed=True)
            rows['during_edit'] = state(scene, space, op)
            if operation.startswith('rollback_'):
                stage = operation.removeprefix('rollback_')

                def fail(point, slot=None):
                    if point == stage:
                        raise RuntimeError('injected edit publication ' + stage)

                core._publication_checkpoint = fail
                try:
                    op.session.confirm()
                except RuntimeError as exc:
                    assert str(exc) == 'injected edit publication ' + stage
                else:
                    raise AssertionError('publication failure was not reached: ' + stage)
                finally:
                    core._publication_checkpoint = checkpoint
                rows['after_rollback'] = state(scene, space, op)
                assert core.live_layout_hash(scene) == before
                assert len(history.HISTORY.entries) == history_count
                assert all(core.get_camera(scene, slot) == camera
                           for slot, camera in original_ids.items())
                assert_bindings(scene, space, case, overview, c2)
                assert_snapshots(scene, op, c2 if viewport_kind == 'C2' else overview)
                assert not op.session.closed and op.session.draft == op.camera
                op.cancel_edit()
            elif operation == 'cancel':
                op.cancel_edit()
            else:
                op.camera = op.session.confirm()
                op.return_to_view(confirmed=True)
                assert op.stage == (return_stage or 'WATCH') and op.session.closed
                rows['after_confirm'] = state(scene, space, op)
                current = core.get_camera(scene, 2)
                assert core.parameters(current) == target
                assert all(core.parameters(core.get_camera(scene, slot)) == before_parameters[slot]
                           for slot in (1, 3))
                assert len(history.HISTORY.entries) == history_count + (operation == 'confirm_changed')
                op.finish()
                rows['after_exit'] = state(scene, space, op)
                assert_bindings(scene, space, case, overview, current)
                expected_view = current if viewport_kind == 'C2' else overview
                assert_snapshots(scene, op, expected_view)
                op.session.snapshot.restore()
                assert_bindings(scene, space, case, overview, current)
                rows['after_session_snapshot_restore'] = state(scene, space, op)
            if operation == 'cancel' or operation.startswith('rollback_'):
                rows['after_cancel'] = state(scene, space, op)
                assert_bindings(scene, space, case, overview, c2)
                assert core.live_layout_hash(scene) == before
                assert len(history.HISTORY.entries) == history_count
                assert all(core.get_camera(scene, slot) == camera
                           for slot, camera in original_ids.items())
            assert panel._ACTIVE is None
            assert not any(obj.get('wfrl_custom_draft') for obj in scene.objects)
            bpy.context.view_layer.update()
            core.validate_native_state(scene)
        rows['status'] = 'PASS'
    except Exception:
        rows['status'] = 'FAIL'
        REPORT['failures'][name] = traceback.format_exc()
    finally:
        core._publication_checkpoint = checkpoint
        cleanup_controller()
        REPORT['checks'][name] = rows
        save_report()


addon.register()
try:
    addon.load_demo_scene()
    scene = bpy.context.scene
    baseline = core.layout_dict(scene)
    overview = scene.camera
    assert overview and overview not in [core.get_camera(scene, slot) for slot in core.SLOTS]
    window = bpy.context.window
    assert window is not None, 'Factory startup must provide a window context'
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    for operation in ('confirm_unchanged', 'confirm_changed', 'cancel',
                      'rollback_references', 'rollback_commit'):
        for case in CASES:
            run_case(scene, window, area, region, overview, baseline, case, operation)
    for return_stage in ('WATCH', 'LAYOUT'):
        run_case(scene, window, area, region, overview, baseline, CASES[1],
                 'confirm_changed', return_stage=return_stage)
    REPORT['status'] = 'FAIL' if REPORT['failures'] else 'PASS'
    save_report()
finally:
    cleanup_controller()
    addon.unregister()

print('CUSTOM_CAMERA_EDIT_BINDING_' + REPORT['status'], len(REPORT['checks']),
      addon.__file__, flush=True)
assert not REPORT['failures'], json.dumps(REPORT['failures'], ensure_ascii=False, indent=2)
