"""Owned live-view resources are released when delayed arrangement fails."""
from types import SimpleNamespace as NS

import pytest

from blender_frontend.tests.test_bt_view_area_isolation import (  # noqa: F401
    _state, down_view, live_views,
)
from blender_frontend.tests.test_live_stream import (  # noqa: F401
    _stream_area, _TestScreen, stream_view,
)


@pytest.mark.parametrize(('side', 'failure'), [
    ('left', 'copy_view'), ('left', 'prepare'), ('left', 'record'), ('right', 'record'),
])
def test_delayed_arrangement_failure_restores_caller_and_releases_owned_split(live_views, monkeypatch, side, failure):
    view, context = live_views.view, live_views.context
    areas = list(context.screen.areas)
    original = [_state(area) for area in areas]
    foreign_area = _stream_area(70, 0)
    foreign = NS(screen=_TestScreen(200, [foreign_area]), workspace=NS(session_uid=210),
                 as_pointer=lambda: 220)
    view.bpy.context.window_manager.windows.append(foreign)
    foreign_state = _state(foreign_area)
    view.start(context, 'rtsp://camera.local/live')
    stream_area = view._find(context.window, view.session()['area'])
    if side == 'left':
        stream_area.x = -107

    def join(*, source_xy, target_xy):
        assert source_xy == (context.area.x + context.area.width // 2, 40)
        target = next(area for area in context.screen.areas
                      if (area.x + area.width // 2, 40) == target_xy)
        assert target is stream_area
        context.screen.areas.remove(target)
        return {'FINISHED'}
    view.bpy.ops.screen.area_join.side_effect = join
    name = dict(copy_view='_copy_view', prepare='_prepare_stream_area', record='_record_layout')[failure]
    original_call = getattr(view, name)
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        result = original_call(*args, **kwargs)
        if not failed:
            failed = True
            raise RuntimeError('injected delayed arrangement failure')
        return result
    monkeypatch.setattr(view, name, fail_once)

    with pytest.raises(RuntimeError, match='injected delayed arrangement failure'):
        view._tick()

    assert view.session() is None and view._HANDLE is None
    assert context.screen.areas == areas
    assert [_state(area) for area in areas] == original
    assert foreign.screen.areas == [foreign_area] and _state(foreign_area) == foreign_state
    assert not view.bpy.data.images and view._LAYOUT_KEY not in context.screen
    assert not view.bpy.app.timers.is_registered(view._tick)
    live_views.worker.return_value.stop.assert_called_once()


def test_start_operator_scrubs_resource_error_url(stream_view):
    view, context, worker = stream_view
    url = 'rtsp://ci_user:ci_password@camera.local/live?token=ci_token'
    context.window_manager.wfrl_live_url = url
    context.window_manager.wfrl_live_size = '1280x720'
    worker.side_effect = RuntimeError(f'Cannot start {url}')
    operator = view.WFRL_OT_LiveStreamStart()
    operator.turbine = 'T1'
    reports = []
    operator.report = lambda _levels, message: reports.append(message)

    assert operator.execute(context) == {'CANCELLED'}
    assert len(reports) == 1 and 'Cannot start' in reports[0]
    assert all(secret not in reports[0] for secret in ('ci_user', 'ci_password', 'ci_token'))
    assert view.session() is None and context.screen.areas == [context.area]
