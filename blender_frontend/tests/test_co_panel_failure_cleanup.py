"""Panel operators retain resource ownership and report unsuccessful operations."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


def gimbal_context():
    region = NS(type='WINDOW', width=640, height=480)
    area = NS(type='VIEW_3D', regions=[region], header_text_set=Mock(), tag_redraw=Mock())
    wm = NS(event_timer_add=Mock(return_value=object()), event_timer_remove=Mock(),
            modal_handler_add=Mock())
    return NS(area=area, scene=NS(), window_manager=wm, window=object())


@pytest.mark.parametrize('preset', ['DOWN', 'NACELLE'])
def test_gimbal_unavailable_preset_preserves_mount_selection(frontend_module, stub_module, monkeypatch, preset):
    panel = frontend_module('wfrl_blender.panels.gimbal')
    stub_module('wfrl_blender.farm_flex', is_active=lambda _scene: False)
    monkeypatch.setattr(panel, 'show', Mock(side_effect=ValueError('camera mount is unavailable')))
    scene = NS(wfrl_gimbal_kind='NACELLE' if preset == 'DOWN' else 'DEMO')
    previous = scene.wfrl_gimbal_kind
    operator = NS(preset=preset, report=Mock())

    assert panel.WFRL_OT_GimbalPreset.execute(operator, NS(scene=scene)) == {'CANCELLED'}
    assert scene.wfrl_gimbal_kind == previous
    assert 'camera mount is unavailable' in operator.report.call_args[0][1]


@pytest.mark.parametrize('fail_at', ['output', 'frame'])
def test_gimbal_failed_view_switch_restores_output_and_view(frontend_module, stub_module, monkeypatch, fail_at):
    panel = frontend_module('wfrl_blender.panels.gimbal')
    stub_module('wfrl_blender.split_reconstruction', is_reconstruction_view=lambda *_args: False)
    stub_module('wfrl_blender.panels.custom_cameras', dismiss_for_view_switch=Mock())

    class Scene(dict):
        pass

    scene = Scene(wfrl_nacelle_previous_output=[100, 200, 1., 1.])
    scene.render = NS(resolution_x=320, resolution_y=200, pixel_aspect_x=1., pixel_aspect_y=1.)
    original_camera = object()
    view = NS(view_perspective='PERSP', view_camera_zoom=12., view_camera_offset=(.1, .2))
    space = NS(type='VIEW_3D', use_local_camera=False, camera=original_camera, region_3d=view)
    context = NS(scene=scene, space_data=space, area=object())
    monkeypatch.setattr(panel, 'current', lambda _scene: object())

    def output(_scene, _camera):
        scene.render.resolution_x = 800
        scene.render.pixel_aspect_y = 1.2
        del scene['wfrl_nacelle_previous_output']
        if fail_at == 'output':
            raise RuntimeError('output failed')

    stub_module('wfrl_blender.nacelle_camera', select_output=output)
    monkeypatch.setattr(panel, 'fill_camera_view', Mock(side_effect=RuntimeError('frame failed')))

    with pytest.raises(RuntimeError, match='failed'):
        panel.show(context)
    assert vars(scene.render) == dict(resolution_x=320, resolution_y=200, pixel_aspect_x=1., pixel_aspect_y=1.)
    assert scene['wfrl_nacelle_previous_output'] == [100, 200, 1., 1.]
    assert space.camera is original_camera and not space.use_local_camera
    assert vars(view) == dict(view_perspective='PERSP', view_camera_zoom=12., view_camera_offset=(.1, .2))


@pytest.mark.parametrize('step', ['timer', 'draw', 'modal', 'header', 'show'])
def test_gimbal_failed_start_releases_acquired_resources(frontend_module, monkeypatch, step):
    panel = frontend_module('wfrl_blender.panels.gimbal')
    context = gimbal_context()
    show = Mock()
    monkeypatch.setattr(panel, 'show', show)
    operator = panel.WFRL_OT_GimbalMode()
    operator.report = Mock()
    view = panel.bpy.types.SpaceView3D
    operations = dict(timer=context.window_manager.event_timer_add,
        draw=view.draw_handler_add, modal=context.window_manager.modal_handler_add,
        header=context.area.header_text_set, show=show)
    operations[step].side_effect = ValueError('injected startup failure')

    assert operator.invoke(context, object()) == {'CANCELLED'}
    assert panel._ACTIVE is None
    assert operator._closed
    if step != 'timer':
        context.window_manager.event_timer_remove.assert_called_once()
    if step not in {'timer', 'draw'}:
        view.draw_handler_remove.assert_called_once()
    if step != 'show':
        show.assert_not_called()
    assert 'injected startup failure' in operator.report.call_args[0][1]


@pytest.mark.parametrize('error', [ReferenceError('removed timer'), RuntimeError('timer remove failed')])
def test_gimbal_cleanup_attempts_every_resource_and_can_retry(frontend_module, monkeypatch, error):
    panel = frontend_module('wfrl_blender.panels.gimbal')
    context = gimbal_context()
    monkeypatch.setattr(panel, 'show', Mock())
    operator = panel.WFRL_OT_GimbalMode()
    operator.report = Mock()
    assert operator.invoke(context, object()) == {'RUNNING_MODAL'}
    context.window_manager.event_timer_remove.side_effect = error

    operator.finish(context)
    panel.bpy.types.SpaceView3D.draw_handler_remove.assert_called_once()
    assert operator._handle is None
    assert operator._closed
    if isinstance(error, RuntimeError):
        assert panel._ACTIVE is operator
        assert operator._timer is not None
        assert 'timer remove failed' in operator.report.call_args[0][1]
        context.window_manager.event_timer_remove.side_effect = None
        operator.finish(context)
    assert panel._ACTIVE is None
    assert operator._timer is None
    assert operator._scene is None
    operator.finish(context)
    panel.bpy.types.SpaceView3D.draw_handler_remove.assert_called_once()


def test_gimbal_modal_numeric_error_cancels_with_report(frontend_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.gimbal')
    context = gimbal_context()
    camera = object()
    context.scene = NS(wfrl_gimbal_kind='DEMO', wfrl_gimbal_turbine='T1',
                       objects={'WFRL.Camera.T1.Gimbal': camera})
    context.area.spaces = NS(active=NS(camera=camera))
    monkeypatch.setattr(panel, 'show', Mock())
    monkeypatch.setattr(panel, 'change', Mock(side_effect=ValueError('Camera angles must be finite')))
    operator = panel.WFRL_OT_GimbalMode()
    operator.report = Mock()
    operator.inside = lambda _event: True
    context.area.regions[0].x = context.area.regions[0].y = 0
    assert operator.invoke(context, object()) == {'RUNNING_MODAL'}
    assert operator.modal(context, NS(type='WHEELUPMOUSE', mouse_x=100, mouse_y=100)) == {'CANCELLED'}
    assert panel._ACTIVE is None
    assert 'finite' in operator.report.call_args[0][1]


def test_gimbal_history_handlers_cancel_before_rna_invalidation(frontend_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.gimbal')
    handlers = NS(load_pre=[], undo_pre=[], redo_pre=[])
    monkeypatch.setattr(panel.bpy.app, 'handlers', handlers)
    panel.register_properties()
    panel.register_properties()
    for name in ('load_pre', 'undo_pre', 'redo_pre'):
        callbacks = getattr(handlers, name)
        assert callbacks == [panel.cancel_on_load]
        active = Mock()
        monkeypatch.setattr(panel, '_ACTIVE', active)
        callbacks[0](None)
        active.finish.assert_called_once_with()
    monkeypatch.setattr(panel, '_ACTIVE', None)
    panel.unregister_properties()
    assert all(not getattr(handlers, name) for name in ('load_pre', 'undo_pre', 'redo_pre'))


@pytest.mark.parametrize('kind', ['Export', 'Stream'])
def test_video_watcher_failure_stops_only_the_job_it_started(frontend_module, monkeypatch, kind):
    panel = frontend_module('wfrl_blender.panels.video_output')
    job = NS(active=False, start=Mock(), stop=Mock(), error='', message='', state='IDLE')
    job.start.side_effect = lambda *_args: setattr(job, 'active', True)
    other = NS(active=False)
    monkeypatch.setattr(panel, 'service', NS(export=job if kind == 'Export' else other,
                                           stream=job if kind == 'Stream' else other))
    monkeypatch.setattr(panel, '_source', lambda _context: '/source.mp4')
    monkeypatch.setattr(panel, '_watch', Mock(side_effect=RuntimeError('timer registration failed')))
    monkeypatch.setattr(panel.bpy.path, 'abspath', lambda path: path)
    monkeypatch.setattr(panel, 'get_preferences', lambda _context: NS(video_ffmpeg='',
        video_mediamtx='', video_rtsp_port=8554, video_rtsp_lan=False))
    operator = NS(filepath='/output.mp4', report=Mock())
    execute = getattr(panel, 'WFRL_OT_Video' + kind).execute

    assert execute(operator, object()) == {'CANCELLED'}
    job.stop.assert_called_once()
    assert 'timer registration failed' in operator.report.call_args[0][1]
    job.stop.reset_mock()
    job.start.side_effect = RuntimeError('already active')
    assert execute(operator, object()) == {'CANCELLED'}
    job.stop.assert_not_called()


def test_video_poll_error_requests_owned_process_cleanup(frontend_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.video_output')
    stream = NS(active=True, poll=Mock(side_effect=OSError('log read failed')), fail=Mock())
    monkeypatch.setattr(panel, 'service', NS(stream=stream, export=NS(active=False),
                                           status_for=lambda: ('STOPPING',)))
    monkeypatch.setattr(panel.bpy, 'context', NS(window_manager=NS(windows=[])))

    assert panel._tick() == .25
    stream.fail.assert_called_once()
    assert 'log read failed' in stream.fail.call_args[0][0]


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_clearance_nonfinite_readout_is_unavailable(frontend_module, value):
    panel = frontend_module('wfrl_blender.panels.clearance')
    assert panel.number(value) == '--'
    assert panel.number(value, signed=True) == '--'


def test_clearance_load_runtime_error_clears_and_reports(frontend_module, stub_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.clearance')
    playback = stub_module('wfrl_blender.playback', cancel=Mock())
    monkeypatch.setattr(panel.clearance_replay, 'load', Mock(side_effect=RuntimeError('scene binding failed')))
    clear = Mock()
    monkeypatch.setattr(panel.clearance_replay, 'clear', clear)
    scene = NS(wfrl_clearance_normal_path='/package', wfrl_clearance_show_config=False)
    operator = NS(demo='normal', report=Mock())

    assert panel.WFRL_OT_ClearanceClip.execute(operator, NS(scene=scene)) == {'CANCELLED'}
    playback.cancel.assert_called_once_with(scene)
    clear.assert_called_once_with(scene, '回放数据未就绪：scene binding failed')
    assert scene.wfrl_clearance_show_config
    assert 'scene binding failed' in operator.report.call_args[0][1]
