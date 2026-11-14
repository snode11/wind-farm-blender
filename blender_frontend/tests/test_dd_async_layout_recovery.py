"""Asynchronous screen operations restore the caller's layout after failure."""
from contextlib import contextmanager
from copy import deepcopy
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import cameras


class Scene(dict):
    pass


class Area:
    def __init__(self, x, y, width, height, label):
        self.x, self.y, self.width, self.height = x, y, width, height
        self.type, self.label = 'VIEW_3D', label
        self.regions = [NS(type='UI', width=180, active_panel_category='Item')]
        self.spaces = NS(active=NS(
            use_local_camera=True, camera=None, lock_camera=True, lens=43.,
            clip_start=.2, clip_end=789., show_region_ui=True, show_gizmo=False,
            shading=NS(type='SOLID', light='STUDIO'), overlay=NS(show_overlays=False),
            region_3d=NS(view_perspective='ORTHO', view_distance=23.,
                         view_location=[1., 2., 3.], view_rotation=[1., 0., 0., 0.],
                         view_matrix=[[1., 0.], [0., 1.]], view_camera_zoom=17.,
                         view_camera_offset=[.2, -.3])))

    def as_pointer(self):
        return id(self)

    def tag_redraw(self):
        pass


def screen_state(screen):
    return sorted((area.x, area.y, area.width, area.height,
                   deepcopy(vars(area.spaces.active.region_3d)),
                   area.spaces.active.camera, area.spaces.active.use_local_camera,
                   area.spaces.active.show_region_ui, area.spaces.active.lock_camera,
                   vars(area.spaces.active.shading).copy(), vars(area.spaces.active.overlay).copy(),
                   area.regions[0].active_panel_category) for area in screen.areas)


@pytest.fixture
def layout_runtime(monkeypatch):
    scene = Scene(wfrl_view_layout='CUSTOM')
    names = ('WFRL.Camera.World', 'WFRL.Camera.T1.Sensor',
             'WFRL.Camera.Top', 'WFRL.Camera.Side')
    scene.objects = {name: NS(name=name, type='CAMERA') for name in names}
    scene.camera = NS(name='original camera')
    area = Area(0, 0, 800, 600, 'caller')
    screen = NS(areas=[area])
    window = NS(screen=screen, scene=scene)
    other = NS(areas=[Area(0, 0, 1000, 700, 'other window')])
    context = NS(scene=scene, screen=screen, window=window, area=area)
    timers, operations = set(), []
    runtime = NS(context=context, scene=scene, window=window, screen=screen,
                 other=other, timers=timers, operations=operations,
                 split_error=None, join_error=None)

    @contextmanager
    def override(**values):
        assert values['window'] is window and values.get('screen', screen) is screen
        runtime.current = values['area']
        yield

    def split(direction, factor):
        if runtime.split_error:
            raise runtime.split_error
        source = runtime.current
        clone = Area(source.x, source.y, source.width, source.height, 'split')
        clone.spaces = deepcopy(source.spaces)
        if direction == 'VERTICAL':
            source.width = round(source.width * factor)
            clone.x += source.width
            clone.width -= source.width
        else:
            source.height = round(source.height * factor)
            clone.y += source.height
            clone.height -= source.height
        screen.areas.append(clone)
        operations.append(('split', source, clone))
        return {'FINISHED'}

    def at(point):
        x, y = point
        return next(a for a in screen.areas
                    if a.x <= x < a.x + a.width and a.y <= y < a.y + a.height)

    def join(source_xy, target_xy):
        if runtime.join_error:
            raise runtime.join_error
        source, target = at(source_xy), at(target_xy)
        assert source is not target
        left, bottom = min(source.x, target.x), min(source.y, target.y)
        right = max(source.x + source.width, target.x + target.width)
        top = max(source.y + source.height, target.y + target.height)
        assert (right - left) * (top - bottom) == (
            source.width * source.height + target.width * target.height)
        source.x, source.y, source.width, source.height = left, bottom, right - left, top - bottom
        screen.areas.remove(target)
        operations.append(('join', source, target))
        return {'FINISHED'}

    runtime.split, runtime.join = split, join
    monkeypatch.setitem(sys.modules, 'bpy', NS(
        context=NS(temp_override=override),
        app=NS(background=False, timers=NS(is_registered=lambda f: f in timers,
            register=lambda f, **kwargs: timers.add(f), unregister=timers.remove)),
        ops=NS(screen=NS(area_split=split, area_join=join))))
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', None)
    monkeypatch.setattr(cameras, 'fill_camera_view', lambda *_args: None)
    return runtime


def finish_timers(limit=30):
    for _ in range(limit):
        if cameras._layout_timer() is None:
            return
    pytest.fail('layout did not finish or recover')


@pytest.mark.parametrize('boundary', ['split', 'finish'])
def test_timer_failure_restores_split_and_caller_view(layout_runtime, monkeypatch, boundary):
    r = layout_runtime
    before, outside, camera = screen_state(r.screen), screen_state(r.other), r.scene.camera
    cameras.set_view_layout(r.context, 'QUAD')
    assert len(r.screen.areas) == 2
    if boundary == 'split':
        r.split_error = RuntimeError('later split failed')
    else:
        def fail(*_args):
            raise RuntimeError('later framing failed')
        monkeypatch.setattr(cameras, 'fill_camera_view', fail)
    finish_timers()
    assert screen_state(r.screen) == before
    assert screen_state(r.other) == outside
    assert r.scene.camera is camera and r.scene['wfrl_view_layout'] == 'CUSTOM'
    assert 'later ' in r.scene['wfrl_view_layout_error']
    assert cameras._LAYOUT_JOB is None


def test_timer_failure_recreates_joined_views(layout_runtime, monkeypatch):
    r = layout_runtime
    r.screen.areas = [Area(x, y, 400, 300, f'{x}-{y}') for x in (0, 400) for y in (0, 300)]
    for index, area in enumerate(r.screen.areas):
        area.spaces.active.region_3d.view_location = [index, 3., 9.]
        area.spaces.active.shading.type = 'MATERIAL' if index % 2 else 'SOLID'
    before = screen_state(r.screen)
    cameras.set_view_layout(r.context, 'SINGLE')
    assert len(r.screen.areas) == 3
    r.join_error = RuntimeError('later join failed')
    finish_timers()
    assert screen_state(r.screen) == before
    assert 'later join failed' in r.scene['wfrl_view_layout_error']


def test_failed_recovery_keeps_original_error_and_retries(layout_runtime, monkeypatch):
    r = layout_runtime
    before = screen_state(r.screen)
    cameras.set_view_layout(r.context, 'DUAL')
    primary = RuntimeError('framing broke')
    monkeypatch.setattr(cameras, 'fill_camera_view', lambda *_: (_ for _ in ()).throw(primary))
    r.join_error = RuntimeError('recovery join blocked')
    cameras._layout_timer()
    cameras._layout_timer()
    assert cameras._LAYOUT_JOB is not None
    assert cameras._LAYOUT_JOB['error'] is primary
    assert 'framing broke' in r.scene['wfrl_view_layout_error']
    assert 'recovery join blocked' in r.scene['wfrl_view_layout_error']
    r.join_error = None
    finish_timers()
    assert screen_state(r.screen) == before
    assert 'framing broke' in r.scene['wfrl_view_layout_error']


def test_synchronous_failure_after_split_keeps_original_exception(layout_runtime, monkeypatch):
    r = layout_runtime
    before = screen_state(r.screen)
    primary = RuntimeError('timer registration failed')
    scheduled = [False]

    def schedule():
        if not scheduled[0]:
            scheduled[0] = True
            raise primary

    monkeypatch.setattr(cameras, '_layout_schedule', schedule)
    with pytest.raises(RuntimeError) as caught:
        cameras.set_view_layout(r.context, 'DUAL')
    assert caught.value is primary
    finish_timers()
    assert screen_state(r.screen) == before


def test_deferred_recovery_preserves_synchronous_caller_rollback(layout_runtime, monkeypatch):
    r = layout_runtime
    before = screen_state(r.screen)
    view = r.context.area.spaces.active.region_3d
    view.view_perspective = 'CAMERA'
    calls = []

    def schedule():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('timer registration failed')

    monkeypatch.setattr(cameras, '_layout_schedule', schedule)
    with pytest.raises(RuntimeError, match='timer registration failed'):
        cameras.set_view_layout(r.context, 'DUAL')
    view.view_perspective = 'ORTHO'
    finish_timers()
    assert screen_state(r.screen) == before


def test_changed_window_defers_rollback_until_original_screen_returns(layout_runtime):
    r = layout_runtime
    before, outside = screen_state(r.screen), screen_state(r.other)
    cameras.set_view_layout(r.context, 'DUAL')
    r.window.screen = r.other
    cameras._layout_timer()
    assert cameras._LAYOUT_JOB is not None
    assert screen_state(r.other) == outside
    r.window.screen = r.screen
    finish_timers()
    assert screen_state(r.screen) == before


@pytest.mark.parametrize(('layout', 'count'), [('SINGLE', 1), ('DUAL', 2), ('QUAD', 4)])
def test_success_keeps_existing_layout_and_camera_assignment(layout_runtime, layout, count):
    r = layout_runtime
    cameras.set_view_layout(r.context, layout)
    finish_timers()
    assert len(r.screen.areas) == count
    assert r.scene['wfrl_view_layout'] == layout
    assert all(area.spaces.active.region_3d.view_perspective == 'CAMERA' for area in r.screen.areas)
    assert not r.scene['wfrl_view_layout_error']


def test_layout_snapshot_retains_locked_object_without_allocating(layout_runtime, monkeypatch):
    r = layout_runtime

    class LockedObject:
        bl_rna = object()

        def copy(self):
            pytest.fail('snapshot must not allocate an Object datablock')

    target = LockedObject()
    r.context.area.spaces.active.lock_object = target
    monkeypatch.setattr(cameras, 'fill_camera_view', lambda *_: (_ for _ in ()).throw(RuntimeError('framing failed')))
    with pytest.raises(RuntimeError, match='framing failed'):
        cameras.set_view_layout(r.context, 'SINGLE')
    assert r.context.area.spaces.active.lock_object is target


def test_cleanup_preserves_pending_recovery_until_retry_succeeds(layout_runtime, monkeypatch):
    r = layout_runtime
    before = screen_state(r.screen)
    cameras.set_view_layout(r.context, 'DUAL')
    monkeypatch.setattr(cameras, 'fill_camera_view', lambda *_: (_ for _ in ()).throw(RuntimeError('framing failed')))
    r.join_error = RuntimeError('join blocked')
    cameras._layout_timer()
    cameras.cancel_view_layout()
    assert cameras._LAYOUT_JOB is not None
    assert cameras._layout_timer in r.timers
    r.join_error = None
    finish_timers()
    assert screen_state(r.screen) == before


def test_rollback_retains_caller_area_when_blender_splits_to_left(layout_runtime, monkeypatch):
    r = layout_runtime
    caller = r.context.area

    def split(**kwargs):
        result = r.split(**kwargs)
        source, clone = r.operations[-1][1:]
        source.x, clone.x = clone.x, source.x
        return result

    monkeypatch.setattr(sys.modules['bpy'].ops.screen, 'area_split', split)
    monkeypatch.setattr(cameras, 'fill_camera_view', lambda *_: (_ for _ in ()).throw(RuntimeError('framing failed')))
    cameras.set_view_layout(r.context, 'DUAL')
    finish_timers()
    assert r.screen.areas == [caller]


def test_recovery_restores_sidebar_category_before_hiding_region(layout_runtime, monkeypatch):
    r = layout_runtime
    space = r.context.area.spaces.active
    space.show_region_ui = False

    class Sidebar:
        type, width, category = 'UI', 180, 'Item'

        @property
        def active_panel_category(self):
            return self.category

        @active_panel_category.setter
        def active_panel_category(self, value):
            if not space.show_region_ui:
                raise RuntimeError('hidden sidebar is not editable')
            self.category = value

    r.context.area.regions = [Sidebar()]
    monkeypatch.setattr(cameras, 'fill_camera_view', lambda *_: (_ for _ in ()).throw(RuntimeError('framing failed')))
    cameras.set_view_layout(r.context, 'SINGLE', camera_names=('WFRL.Camera.World',))
    finish_timers()
    assert not space.show_region_ui
    assert r.context.area.regions[0].category == 'Item'
