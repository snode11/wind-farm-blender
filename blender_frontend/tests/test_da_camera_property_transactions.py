"""Camera field validation and unregister cleanup with host-side RNA adapters."""
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_cameras as core


@pytest.fixture
def editor(frontend_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.custom_cameras')
    properties = frontend_module('wfrl_blender.panels.custom_camera_properties')
    params = core.CameraParameters((1., 2., 3.))
    camera = {'custom_label': 'saved label'}
    state = NS(params=params, applied=[], redraws=[])
    wm = NS(wfrl_custom_x=1., wfrl_custom_y=2., wfrl_custom_z=3.,
            wfrl_custom_yaw=0., wfrl_custom_pitch=0., wfrl_custom_roll=0.,
            wfrl_custom_fov=75., wfrl_custom_vfov=params.vfov,
            wfrl_custom_has_focal=False, wfrl_custom_focal=35.,
            wfrl_custom_long_edge=params.output_long_edge_px, wfrl_custom_near=.005,
            wfrl_custom_far=30000., wfrl_custom_label='new label',
            wfrl_custom_mount_mode='SURFACE', wfrl_custom_research_confirmed=False,
            wfrl_custom_distance=.1)

    def apply(camera, value, **_kwargs):
        core.validate_parameters(value)
        state.applied.append(value)
        state.params = value

    api = NS(CameraParameters=core.CameraParameters, parameters=lambda _camera: state.params,
             apply_parameters=apply, apply_research=lambda _scene, camera, value, _confirmed: apply(camera, value),
             anchor=lambda _camera: NS(distance=.1), set_distance=lambda *_args: None)
    op = NS(scene=object(), camera=camera, stage='AIM', slot=1, ready=True, error='')
    for name, value in dict(core=api, _ACTIVE=op, _SYNCING=False,
                            _redraw_if_changed=state.redraws.append).items():
        monkeypatch.setattr(panel, name, value)
    return NS(panel=panel, properties=properties, wm=wm, op=op, api=api, state=state)


@pytest.mark.parametrize('mode', ['SURFACE', 'RESEARCH'])
@pytest.mark.parametrize('fov', [-1., float('nan'), float('inf')])
def test_rejected_numeric_fields_do_not_change_the_camera_label(editor, mode, fov):
    editor.wm.wfrl_custom_mount_mode = mode
    editor.wm.wfrl_custom_fov = fov
    editor.properties.fields_changed(editor.wm, NS())
    assert not editor.op.ready and editor.op.error
    assert editor.op.camera == {'custom_label': 'saved label'}
    assert not editor.state.applied and editor.state.redraws == [editor.op]


@pytest.mark.parametrize('mode', ['SURFACE', 'RESEARCH'])
def test_valid_fields_keep_numeric_and_label_updates(editor, mode):
    editor.wm.wfrl_custom_mount_mode = mode
    editor.wm.wfrl_custom_fov = 80.
    editor.properties.fields_changed(editor.wm, NS())
    assert editor.op.ready and not editor.op.error
    assert editor.state.params.fov == 80. and editor.op.camera['custom_label'] == 'new label'


@pytest.mark.parametrize('callback', ['fields_changed', 'distance_changed'])
def test_deleted_camera_reference_marks_field_edit_unready(editor, callback):
    def deleted(*_args):
        raise ReferenceError('StructRNA of type Object has been removed')

    if callback == 'fields_changed':
        editor.api.parameters = deleted
    else:
        editor.api.set_distance = deleted
    getattr(editor.properties, callback)(editor.wm, NS())
    assert not editor.op.ready and 'removed' in editor.op.error
    assert editor.op.camera == {'custom_label': 'saved label'}
    assert editor.state.redraws == [editor.op]


@pytest.mark.parametrize('callback', ['fields_changed', 'distance_changed'])
@pytest.mark.parametrize('area_failure', [False, True])
def test_deleted_camera_during_real_redraw_does_not_escape_field_callback(
        editor, frontend_module, monkeypatch, callback, area_failure):
    overlay = frontend_module('wfrl_blender.panels.custom_camera_overlay')

    def deleted(*_args):
        raise ReferenceError('StructRNA of type Object has been removed')

    def redraw():
        editor.state.redraws.append('area')
        if area_failure:
            raise RuntimeError('area redraw unavailable')

    editor.op.closed = False
    editor.op.camera = NS(as_pointer=deleted)
    editor.op.area = NS(tag_redraw=redraw)
    editor.api.parameters = editor.api.set_distance = deleted
    monkeypatch.setattr(editor.panel, '_redraw_if_changed', overlay._redraw_if_changed)
    getattr(editor.properties, callback)(editor.wm, NS())
    assert not editor.op.ready and 'removed' in editor.op.error
    assert editor.state.redraws == ['area']
    if area_failure:
        assert 'area redraw unavailable' in editor.op.error


@pytest.mark.parametrize('outer_sync', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_field_sync_restores_the_callers_reentrancy_guard(editor, outer_sync, failure):
    editor.panel._SYNCING = outer_sync
    if failure:
        def invalid(_camera):
            raise ReferenceError('StructRNA has been removed')
        editor.api.parameters = invalid
        with pytest.raises(ReferenceError):
            editor.properties.sync_fields(editor.wm, editor.op.camera)
    else:
        editor.properties.sync_fields(editor.wm, editor.op.camera)
    assert editor.panel._SYNCING is outer_sync


@pytest.mark.parametrize('failed_action', ['native', 'active', 'capture', 'preview', 'timer'])
def test_unregister_failure_still_cleans_independent_resources_and_retries(
        frontend_module, stub_module, monkeypatch, failed_action):
    panel = frontend_module('wfrl_blender.panels.custom_cameras')
    properties = frontend_module('wfrl_blender.panels.custom_camera_properties')
    calls, failure = [], [failed_action]

    def action(name):
        def run(*_args, **_kwargs):
            calls.append(name)
            if failure[0] == name:
                raise RuntimeError(name + ' cleanup unavailable')
        return run

    migrate, watchdog, load = (lambda: None for _ in range(3))
    registered = {migrate, watchdog}

    def unregister_timer(callback):
        action('timer')()
        registered.remove(callback)

    window_manager = type('WindowManager', (), {'wfrl_custom_x': object()})
    handlers = NS(load_pre=[load], load_post=[migrate])
    bpy = NS(app=NS(handlers=handlers, timers=NS(is_registered=registered.__contains__,
                                               unregister=unregister_timer)),
             types=NS(WindowManager=window_manager))
    values = dict(bpy=bpy, _ACTIVE=NS(finish=action('active')),
                  migrate_loaded=migrate, lifecycle_watchdog=watchdog, cancel_on_load=load,
                  preview=NS(shutdown=action('preview'), unregister_change_handler=action('changes')),
                  history=NS(HISTORY=NS(clear=action('history'))),
                  core=NS(cleanup_retired=action('retired')))
    for name, value in values.items():
        monkeypatch.setattr(panel, name, value)
    stub_module('wfrl_blender.panels.stacked_camera_rig', unregister_settings=action('stacked'))
    stub_module('wfrl_blender.native_camera_views', unregister=action('native'))
    stub_module('wfrl_blender.custom_camera_capture', shutdown=action('capture'))
    stub_module('wfrl_blender.panels.custom_camera_output', unregister_settings=action('output'))

    with pytest.raises(RuntimeError, match=failed_action + ' cleanup unavailable'):
        properties.unregister_properties()
    assert {'stacked', 'native', 'active', 'capture', 'preview', 'changes', 'output', 'history', 'retired'} <= set(calls)
    assert not handlers.load_pre and not handlers.load_post
    assert registered == ({migrate, watchdog} if failed_action == 'timer' else set())
    failure[0] = None
    properties.unregister_properties()
    assert not registered and not hasattr(window_manager, 'wfrl_custom_x')
