"""X100 operator context, failure rollback and read-only panel behavior."""
import math
import sys
from types import SimpleNamespace as NS

import pytest


class Vector(tuple):
    def copy(self):
        return Vector(self)

    def normalized(self):
        size = math.sqrt(sum(value * value for value in self))
        return Vector(value / size for value in self)

    def __mul__(self, value):
        return Vector(item * value for item in self)

    def __neg__(self):
        return self * -1

    def to_track_quat(self, *_args):
        return ('rotation', *self)


class Axes:
    def normalized(self):
        return self

    def __matmul__(self, other):
        return other


class Scene(dict):
    def as_pointer(self):
        return id(self)


def area():
    region = NS(view_perspective='CAMERA', view_location=Vector((1., 2., 3.)),
                view_rotation=(1., 0., 0., 0.), view_distance=20.)
    return NS(type='VIEW_3D', spaces=NS(active=NS(region_3d=region, region_quadviews=(),
                                               clip_start=.1)), tag_redraw=lambda: None)


@pytest.fixture
def radar(frontend_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.radar_x100')
    runtime = frontend_module('wfrl_blender.radar_x100.runtime')
    view = frontend_module('wfrl_blender.radar_x100.view')
    scene = Scene()
    mount = NS(name='radar', children=[], matrix_world=NS(
        translation=Vector((5., 6., 7.)), to_3x3=lambda: Axes()))
    scene.objects = {mount.name: mount}
    scene.wfrl_x100_replay_s = 0.
    active, other = area(), area()
    context = NS(scene=scene, area=active, screen=NS(areas=[other, active]))
    state = NS(session=NS(mount=mount), reports=[])
    monkeypatch.setattr(runtime, 'session_for', lambda _scene: state.session)
    monkeypatch.setattr(sys.modules['mathutils'], 'Vector', Vector)

    def operator(cls):
        op = cls()
        op.report = lambda levels, text: state.reports.append((levels, text))
        return op

    return NS(panel=panel, runtime=runtime, view=view, context=context, state=state,
              mount=mount, other=other, operator=operator)


def test_frame_unit_changes_only_the_calling_3d_area(radar):
    before = vars(radar.other.spaces.active.region_3d).copy()
    op = radar.operator(radar.panel.WFRL_OT_X100FrameUnit)
    assert op.poll(radar.context)
    assert op.execute(radar.context) == {'FINISHED'}
    assert vars(radar.other.spaces.active.region_3d) == before
    assert radar.other.spaces.active.clip_start == .1
    local = radar.context.area.spaces.active
    assert local.region_3d.view_location == (5., 6., 7.)
    assert local.region_3d.view_distance == 1.4 and local.clip_start == .01


@pytest.mark.parametrize('invalid', ['no_area', 'wrong_area', 'no_session', 'no_mount', 'replaced_mount', 'deleted_mount'])
def test_frame_poll_and_execute_reject_the_same_invalid_context(radar, invalid):
    if invalid == 'no_area':
        radar.context.area = None
    elif invalid == 'wrong_area':
        radar.context.area.type = 'IMAGE_EDITOR'
    elif invalid == 'no_session':
        radar.state.session = None
    elif invalid == 'no_mount':
        radar.state.session.mount = None
    elif invalid == 'replaced_mount':
        radar.context.scene.objects['radar'] = object()
    else:
        class Deleted:
            @property
            def name(self):
                raise ReferenceError('StructRNA has been removed')
        radar.state.session.mount = Deleted()
    before = vars(radar.other.spaces.active.region_3d).copy()
    op = radar.operator(radar.panel.WFRL_OT_X100FrameUnit)
    assert not op.poll(radar.context)
    assert op.execute(radar.context) == {'CANCELLED'}
    assert radar.state.reports and vars(radar.other.spaces.active.region_3d) == before


def test_frame_unit_restores_partial_view_changes_on_failure(radar, monkeypatch):
    space = radar.context.area.spaces.active
    before = vars(space.region_3d).copy()

    def failed_frame(context, _mount):
        for current in context.screen.areas:
            current.spaces.active.region_3d.view_perspective = 'PERSP'
            current.spaces.active.region_3d.view_location = Vector((99., 98., 97.))
            current.spaces.active.clip_start = .01
        raise RuntimeError('view update unavailable')

    monkeypatch.setattr(radar.view, 'frame_unit', failed_frame)
    op = radar.operator(radar.panel.WFRL_OT_X100FrameUnit)
    assert op.execute(radar.context) == {'CANCELLED'}
    assert vars(space.region_3d) == before and space.clip_start == .1
    assert radar.other.spaces.active.region_3d.view_perspective == 'CAMERA'
    assert radar.state.reports == [({'ERROR'}, 'view update unavailable')]


def test_step_reports_index_failure_and_still_pauses(radar):
    def fail_index():
        raise ValueError('replay index unavailable')

    radar.state.session = NS(mode='REPLAY', replay=NS(rows=[0, 1]), playing=True,
                             selected_replay_index=fail_index)
    op = radar.operator(radar.panel.WFRL_OT_X100Step)
    op.direction = 1
    assert op.execute(radar.context) == {'CANCELLED'}
    assert radar.state.session.playing is False
    assert radar.state.reports == [({'ERROR'}, 'replay index unavailable')]
    assert radar.context.scene['wfrl_x100_error'] == 'replay index unavailable'


@pytest.mark.parametrize('operator_name', ['WFRL_OT_X100Step', 'WFRL_OT_X100Play'])
@pytest.mark.parametrize('invalid', ['no_session', 'wrong_source', 'single_frame', 'closing'])
def test_replay_operators_reject_unavailable_sessions(radar, operator_name, invalid):
    radar.state.session = NS(mode='REPLAY', replay=NS(rows=[0, 1]), closing=False)
    if invalid == 'no_session':
        radar.state.session = None
    elif invalid == 'wrong_source':
        radar.state.session.mode = 'LIVE'
    elif invalid == 'single_frame':
        radar.state.session.replay = None
    else:
        radar.state.session.closing = True
    op = radar.operator(getattr(radar.panel, operator_name))
    op.direction = 1
    assert not op.poll(radar.context)
    assert op.execute(radar.context) == {'CANCELLED'}


@pytest.mark.parametrize('direction, expected', [(-1, 0), (1, 2), (100, 2)])
def test_step_keeps_valid_index_clamping(radar, direction, expected):
    calls = []
    radar.state.session = NS(mode='REPLAY', replay=NS(rows=[0, 1, 2]), closing=False,
                             playing=True, selected_replay_index=lambda: 1, seek_frame=calls.append)
    op = radar.operator(radar.panel.WFRL_OT_X100Step)
    op.direction = direction
    assert op.poll(radar.context)
    assert op.execute(radar.context) == {'FINISHED'}
    assert calls == [expected] and radar.state.session.playing is False


class Layout:
    def __init__(self, labels=None):
        self.labels = [] if labels is None else labels

    def column(self, **_kwargs):
        return Layout(self.labels)

    def label(self, *, text, **_kwargs):
        self.labels.append(text)


def test_frame_panel_tolerates_a_session_closed_after_poll(radar):
    radar.state.session = NS(frame=object(), client=None)
    assert radar.panel.WFRL_PT_X100Frame.poll(radar.context)
    radar.state.session = None
    panel = NS(layout=Layout())
    radar.panel.WFRL_PT_X100Frame.draw(panel, radar.context)
    assert panel.layout.labels == []


def test_frame_panel_shows_missing_source_time_without_scene_writes(radar, monkeypatch):
    import numpy as np
    display = sys.modules['wfrl_blender.radar_x100.display']
    monkeypatch.setattr(display, 'source_label', lambda _frame: '测试')
    monkeypatch.setattr(display, 'observation_lines', lambda _frame: [])
    frame = NS(stream='UNKNOWN', sequence=1, validity='VALID_EMPTY', points=[],
               finite_mask=np.array([], dtype=bool), time=NS(source_time_s=None, quality='UNKNOWN',
                                                           timestamp_reference='UNKNOWN', clock_domain='test'))
    radar.state.session = NS(frame=frame, client=None)

    class ReadOnlyScene(Scene):
        def __setitem__(self, _key, _value):
            raise AssertionError('panel draw must not write scene data')

        def __setattr__(self, _key, _value):
            raise AssertionError('panel draw must not write scene data')

    radar.context.scene = ReadOnlyScene()
    panel = NS(layout=Layout())
    radar.panel.WFRL_PT_X100Frame.draw(panel, radar.context)
    assert '源时间：未知' in panel.layout.labels
    assert not any('源时间：0' in label for label in panel.layout.labels)
