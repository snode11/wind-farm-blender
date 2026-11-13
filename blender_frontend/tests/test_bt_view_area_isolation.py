"""Calling-area isolation and startup rollback for manual Down and live splits."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from blender_frontend.tests.test_live_stream import _stream_area, stream_view  # noqa: F401


def _state(area):
    space = area.spaces.active
    return dict(camera=space.camera, local=space.use_local_camera,
                view=deepcopy(vars(space.region_3d)), clip=(space.clip_start, space.clip_end),
                shading=vars(space.shading).copy(), overlays=space.overlay.show_overlays,
                region_ui=space.show_region_ui)


@pytest.fixture
def down_view(frontend_module, stub_module, monkeypatch):
    stub_module('bpy_extras.io_utils', ExportHelper=type('ExportHelper', (), {}),
                ImportHelper=type('ImportHelper', (), {}))
    down = frontend_module('wfrl_blender.manual_down')
    cameras = dict(CENTER=object(), LEFT=object(), RIGHT=object())
    ensure = Mock(return_value=cameras['CENTER'])
    monkeypatch.setattr(down, 'ensure_down_camera', ensure)
    monkeypatch.setattr(down, 'ensure_side_cameras',
                        Mock(return_value=dict(Left=cameras['LEFT'], Right=cameras['RIGHT'])))

    def frame(area, _scene):
        region = area.spaces.active.region_3d
        region.view_camera_zoom = 42
        region.view_camera_offset = (0., 0.)

    fit = Mock(side_effect=frame)
    monkeypatch.setattr(down.cameras, 'fill_camera_view', fit)
    operator = down.WFRL_OT_ManualDownView()
    operator.turbine, operator.side, operator.report = 'T1', 'CENTER', Mock()
    return NS(module=down, operator=operator, cameras=cameras, ensure=ensure, fit=fit)


@pytest.mark.parametrize('side', ['CENTER', 'LEFT', 'RIGHT'])
@pytest.mark.parametrize('origin', ['WINDOW', 'UI'])
def test_manual_down_changes_only_calling_area(down_view, side, origin):
    first, caller, last = [_stream_area(index, index * 107) for index in range(1, 4)]
    first.spaces.active.use_local_camera = True
    first.spaces.active.region_3d.view_perspective = 'CAMERA'
    last.spaces.active.region_3d.view_perspective = 'ORTHO'
    original = [_state(area) for area in (first, last)]
    region = caller.regions[0] if origin == 'WINDOW' else NS(type='UI')
    if origin == 'UI':
        caller.regions.append(region)
    scene = NS(render=NS(), camera=object())
    context = NS(area=caller, region=region, scene=scene, screen=NS(areas=[first, caller, last]))
    down_view.operator.side = side

    assert down_view.operator.execute(context) == {'FINISHED'}

    assert caller.spaces.active.use_local_camera
    assert caller.spaces.active.camera is down_view.cameras[side]
    assert caller.spaces.active.region_3d.view_perspective == 'CAMERA'
    assert caller.spaces.active.region_3d.view_camera_zoom == 42
    down_view.fit.assert_called_once_with(caller, scene)
    caller.tag_redraw.assert_called_once()
    assert [_state(area) for area in (first, last)] == original
    first.tag_redraw.assert_not_called()
    last.tag_redraw.assert_not_called()


@pytest.mark.parametrize('unavailable', ['missing', 'wrong_editor', 'no_window', 'no_view', 'foreign_region'])
def test_manual_down_without_calling_view_reports_error_without_fallback(down_view, unavailable):
    caller, other = _stream_area(1, 0), _stream_area(2, 107)
    context = NS(area=caller, region=caller.regions[0], screen=NS(areas=[other, caller]),
                 scene=NS(render=NS(resolution_x=640, resolution_y=480, resolution_percentage=75)))
    if unavailable == 'missing':
        context.area = None
    elif unavailable == 'wrong_editor':
        caller.type = 'IMAGE_EDITOR'
    elif unavailable == 'no_window':
        caller.regions.clear()
        context.region = None
    elif unavailable == 'no_view':
        caller.spaces.active.region_3d = None
    else:
        context.region = other.regions[0]
    original, render = _state(other), vars(context.scene.render).copy()

    assert down_view.operator.execute(context) == {'CANCELLED'}

    down_view.operator.report.assert_called_once()
    levels, message = down_view.operator.report.call_args.args
    assert levels == {'ERROR'} and '三维视图' in message
    down_view.ensure.assert_not_called()
    down_view.fit.assert_not_called()
    assert _state(other) == original and vars(context.scene.render) == render
    other.tag_redraw.assert_not_called()


@pytest.fixture
def live_views(down_view, stream_view, monkeypatch):
    view, context, worker = stream_view
    others = [_stream_area(7, 214), _stream_area(8, 321)]
    others[0].spaces.active.use_local_camera = True
    others[0].spaces.active.region_3d.view_perspective = 'CAMERA'
    others[1].spaces.active.region_3d.view_perspective = 'ORTHO'
    context.screen.areas[:] = [others[0], context.area, others[1]]

    def invoke(**kwargs):
        assert context.temp_override.call_args.kwargs == dict(
            window=context.window, screen=context.screen, area=context.area, region=context.region)
        down_view.operator.turbine, down_view.operator.side = kwargs['turbine'], kwargs['side']
        return down_view.operator.execute(context)

    view.bpy.ops.wfrl.manual_down_view.side_effect = invoke
    return NS(view=view, context=context, worker=worker, others=others, down=down_view)


@pytest.mark.parametrize('split_side', ['left', 'right'])
def test_live_start_and_close_preserve_other_3d_views(live_views, split_side):
    view, context = live_views.view, live_views.context
    original = [_state(area) for area in live_views.others]
    view.start(context, 'rtsp://127.0.0.1:554/camera')
    simulated = _state(context.area)
    assert simulated['camera'] is live_views.down.cameras['CENTER']
    assert simulated['local'] and simulated['view']['view_perspective'] == 'CAMERA'
    assert [_state(area) for area in live_views.others] == original

    if split_side == 'left':
        stream_area = view._find(context.window, view.session()['area'])
        stream_area.x = -107

        def join(*, source_xy, target_xy):
            assert source_xy == (-57, 40) and target_xy == (50, 40)
            context.screen.areas.remove(context.area)
            return {'FINISHED'}

        view.bpy.ops.screen.area_join.side_effect = join
    assert view._tick() == view._TICK_S
    partner = view._partner(context.window, view.session())
    assert partner.spaces.active.camera is live_views.down.cameras['CENTER']
    assert _state(partner)['view'] == simulated['view']
    view.stop()

    assert view.session() is None and len(context.screen.areas) == 3
    assert partner in context.screen.areas
    assert [_state(area) for area in live_views.others] == original
    live_views.worker.return_value.stop.assert_called_once()


@pytest.mark.parametrize('perspective', ['PERSP', 'ORTHO', 'CAMERA'])
@pytest.mark.parametrize('stage', ['down_cancel', 'down_raise', 'frame', 'split_cancel', 'split_raise',
                                  'split_partial_cancel', 'split_partial_raise', 'worker', 'handler', 'timer'])
def test_live_start_failure_restores_calling_view_and_preserves_others(live_views, perspective, stage):
    view, context = live_views.view, live_views.context
    space = context.area.spaces.active
    space.use_local_camera = perspective == 'CAMERA'
    space.region_3d.view_perspective = perspective
    original = _state(context.area)
    others = [_state(area) for area in live_views.others]
    areas = list(context.screen.areas)
    bpy = view.bpy
    if stage.startswith('down_'):
        invoke = bpy.ops.wfrl.manual_down_view.side_effect

        def fail_down(**kwargs):
            invoke(**kwargs)
            space.region_3d.view_matrix[0] = (0, 0, 1, 99)
            space.region_3d.view_location[:] = [90, 80, 70]
            space.region_3d.view_rotation[:] = [0, 1, 0, 0]
            space.region_3d.view_distance = 50
            if stage == 'down_raise':
                raise RuntimeError('injected startup failure')
            return {'CANCELLED'}

        bpy.ops.wfrl.manual_down_view.side_effect = fail_down
    elif stage.startswith('split_'):
        split = bpy.ops.screen.area_split.side_effect

        def fail_split(**kwargs):
            if stage.startswith('split_partial_'):
                split(**kwargs)
            if stage.endswith('raise'):
                raise RuntimeError('injected startup failure')
            return {'CANCELLED'}

        bpy.ops.screen.area_split.side_effect = fail_split
    else:
        fail = dict(frame=live_views.down.fit, worker=live_views.worker,
                    handler=bpy.types.SpaceImageEditor.draw_handler_add, timer=bpy.app.timers.register)[stage]
        fail.side_effect = RuntimeError('injected startup failure')
    reason = ('无法切换' if stage == 'down_cancel' else
              '无法分割' if stage.endswith('cancel') else 'injected startup failure')

    with pytest.raises((RuntimeError, ValueError), match=reason):
        view.start(context, 'rtsp://127.0.0.1:554/camera')

    assert _state(context.area) == original
    assert [_state(area) for area in live_views.others] == others
    assert context.screen.areas == areas
    assert view.session() is None and view._HANDLE is None and not bpy.data.images
    assert not bpy.app.timers.is_registered(view._tick)


def test_live_start_without_calling_area_reports_error_without_fallback(live_views):
    view, context = live_views.view, live_views.context
    areas = list(context.screen.areas)
    original = [_state(area) for area in areas]
    context.area = None

    with pytest.raises(ValueError, match='三维视图'):
        view.start(context, 'rtsp://127.0.0.1:554/camera')

    assert context.screen.areas == areas and [_state(area) for area in areas] == original
    view.bpy.ops.wfrl.manual_down_view.assert_not_called()
    view.bpy.ops.screen.area_split.assert_not_called()
    live_views.worker.assert_not_called()
