"""Failed editor seeks retain the displayed frame, geometry and analysis."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from blender_frontend.tests.test_nrel_editor_playback import editor_runtime


class Coordinates:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=np.float32)

    def __len__(self):
        return len(self.values)

    def foreach_get(self, key, target):
        assert key == 'co'
        target[:] = self.values.ravel()

    def foreach_set(self, key, values):
        assert key == 'co'
        self.values[:] = np.asarray(values).reshape(self.values.shape)


class CurvePoints(Coordinates):
    def add(self, count):
        self.values = np.concatenate([self.values, np.zeros((count, 4), dtype=np.float32)])


class Splines(list):
    def new(self, kind):
        spline = NS(type=kind, points=CurvePoints([[0., 0., 0., 1.]]),
                    use_cyclic_u=False, material_index=0)
        self.append(spline)
        return spline


@pytest.fixture
def timed_editor(editor_runtime, monkeypatch):
    r, s = editor_runtime, editor_runtime.session
    del s.set_time
    scene = s.scene
    scene.frame_current, scene.frame_subframe = 181, .25
    scene.update(defect_time_s=120., defect_analysis_status='EVALUATED',
                 wfrl_clearance_time_s=120., wfrl_flex_frame_valid=True)
    s.records = [{'result': 'evaluated at 120 seconds'}]
    meshes = [NS(vertices=Coordinates([[i, 2., 3.]]), update=Mock()) for i in range(4)]
    objects = [NS(name=name, type='MESH', data=mesh,
                  location=np.array([i, 0., 0.]), rotation_euler=np.array([0., 0., i]),
                  scale=np.ones(3))
               for i, (name, mesh) in enumerate(zip(
                   ['blade', 'tower', 'helper', 'repair'], meshes))]
    scene.objects = objects
    s.helpers, s.repair_source = [objects[2]], objects[3]
    s.flex = NS(times=[117., 177.], visible_turbines={0}, readers=['T1', 'T2'],
                blades=[(objects[0],)], towers=[(objects[1],)],
                comparison=None, frame_error='', enabled=True,
                _world_matrices=None, _update_geometry=None)
    scene.frame_set = Mock(side_effect=lambda frame, subframe: (
        setattr(scene, 'frame_current', frame), setattr(scene, 'frame_subframe', subframe)))
    monkeypatch.setattr(r.namespace, '_UPDATING', False)
    r.objects, r.meshes = objects, meshes
    return r


@pytest.mark.parametrize('outer_guard', [False, True])
@pytest.mark.parametrize('boundary', ['frame', 'deformation', 'after_update'])
def test_set_time_failure_restores_complete_display(timed_editor, monkeypatch, boundary, outer_guard):
    r, s = timed_editor, timed_editor.session
    primary = RuntimeError('deformation unavailable')
    records = s.records
    before = [(obj.location.copy(), obj.rotation_euler.copy(), obj.data.vertices.values.copy())
              for obj in r.objects]
    visible = s.flex.visible_turbines
    monkeypatch.setattr(r.namespace, '_UPDATING', outer_guard)

    def fail(*args, **kwargs):
        assert r.namespace._UPDATING
        for obj in r.objects:
            obj.location[:] += 4
            obj.rotation_euler[:] += 1
            obj.data.vertices.values[:] += 9
        if boundary == 'after_update':
            s.scene['defect_time_s'] = s.scene['wfrl_clearance_time_s'] = 121.
            s.scene['defect_analysis_status'] = 'NOT_EVALUATED'
            s.records = None
        raise primary

    s.flex.update = Mock(side_effect=fail)
    if boundary == 'frame':
        set_frame = s.scene.frame_set.side_effect
        def frame_set(frame, subframe):
            set_frame(frame, subframe)
            if frame != 181:
                fail()
        s.scene.frame_set.side_effect = frame_set
    with pytest.raises(RuntimeError) as caught:
        s.set_time(121.)
    assert caught.value is primary
    assert (s.scene.frame_current, s.scene.frame_subframe) == (181, .25)
    assert s.scene['defect_time_s'] == s.scene['wfrl_clearance_time_s'] == 120.
    assert s.scene['defect_analysis_status'] == 'EVALUATED'
    assert s.records is records
    for obj, (location, rotation, points) in zip(r.objects, before):
        np.testing.assert_array_equal(obj.location, location)
        np.testing.assert_array_equal(obj.rotation_euler, rotation)
        np.testing.assert_array_equal(obj.data.vertices.values, points)
    assert s.flex.visible_turbines is visible
    assert r.namespace._UPDATING is outer_guard


def test_set_time_rollback_failure_keeps_primary_error_and_restores_remaining_state(timed_editor):
    r, s = timed_editor, timed_editor.session
    primary = RuntimeError('deformation unavailable')
    secondary = RuntimeError('frame restoration unavailable')
    s.flex.update = Mock(side_effect=primary)
    original = s.scene.frame_set.side_effect
    def frame_set(frame, subframe):
        original(frame, subframe)
        if frame == 181:
            raise secondary
    s.scene.frame_set.side_effect = frame_set
    with pytest.raises(RuntimeError) as caught:
        s.set_time(121.)
    assert caught.value is primary
    assert (s.scene.frame_current, s.scene.frame_subframe) == (181, .25)
    assert s.scene['defect_time_s'] == 120.
    assert any('frame restoration unavailable' in note for note in primary.__notes__)
    assert r.namespace._UPDATING is False


def test_set_time_restores_replay_binding_after_native_update_failure(timed_editor, monkeypatch):
    from wfrl_blender import clearance_replay, farm_flex, playback

    r, s = timed_editor, timed_editor.session
    primary = ValueError('native mesh write failed')
    old_reader = object()
    monkeypatch.setitem(clearance_replay._READERS, s.scene.as_pointer(), old_reader)
    monkeypatch.setattr(playback, 'cancel', Mock())
    monkeypatch.setattr(clearance_replay, 'clear', lambda scene, reason: scene.update(
        wfrl_flex_active=False, wfrl_clearance_status=reason))
    s.flex.scene = s.scene
    s.flex._update_frame = Mock(side_effect=primary)
    s.flex.update = lambda *args, **kwargs: farm_flex.FarmFlex.update(s.flex, *args, **kwargs)
    with pytest.raises(ValueError) as caught:
        s.set_time(121.)
    assert caught.value is primary
    assert s.flex.frame_error == '' and s.scene['wfrl_flex_frame_valid'] is True
    assert clearance_replay._READERS[s.scene.as_pointer()] is old_reader
    assert 'wfrl_home_load_error' not in s.scene
    assert 'wfrl_clearance_status' not in s.scene
    assert (s.scene.frame_current, s.scene.frame_subframe) == (181, .25)


def test_set_time_restores_comparison_curve_visibility_and_rows(timed_editor):
    r, s = timed_editor, timed_editor.session
    splines = Splines()
    line = splines.new('POLY')
    line.points.add(1)
    line.points.values[:] = [[1., 2., 3., 1.], [4., 5., 6., 1.]]
    line.use_cyclic_u, line.material_index = True, 2
    curve = NS(name='WFRL.Deflection.T1.Total', type='CURVE',
               data=NS(splines=splines), hide_viewport=False, hide_render=False, hidden=False)
    curve.hide_get = lambda: curve.hidden
    curve.hide_set = lambda value: setattr(curve, 'hidden', value)
    s.scene.objects.append(curve)
    s.flex.comparison = NS(objects=[curve], rows={1: {'time': 120.}}, labels=['120 seconds'])
    primary = RuntimeError('comparison update failed')

    def fail(*args, **kwargs):
        splines.clear()
        curve.hide_viewport = curve.hide_render = curve.hidden = True
        s.flex.comparison.rows[1] = {'time': 121.}
        s.flex.comparison.labels = []
        raise primary

    s.flex.update = fail
    with pytest.raises(RuntimeError) as caught:
        s.set_time(121.)
    assert caught.value is primary
    assert len(splines) == 1
    np.testing.assert_array_equal(splines[0].points.values,
                                  [[1., 2., 3., 1.], [4., 5., 6., 1.]])
    assert splines[0].use_cyclic_u and splines[0].material_index == 2
    assert not curve.hide_viewport and not curve.hide_render and not curve.hidden
    assert s.flex.comparison.rows == {1: {'time': 120.}}
    assert s.flex.comparison.labels == ['120 seconds']


def test_set_time_removed_scene_does_not_replace_deformation_error(timed_editor):
    r, s = timed_editor, timed_editor.session
    primary = RuntimeError('deformation failed')
    def fail(*args, **kwargs):
        s.scene.as_pointer = Mock(side_effect=ReferenceError('scene removed'))
        raise primary
    s.flex.update = fail
    with pytest.raises(RuntimeError) as caught:
        s.set_time(121.)
    assert caught.value is primary
    assert r.namespace._UPDATING is False


def test_set_time_restores_trail_history_cleared_by_failed_frame(timed_editor, monkeypatch):
    from collections import deque
    from wfrl_blender import tip_tracking

    r, s = timed_editor, timed_editor.session
    trail = NS(scene=s.scene, points={1: deque([(1., 2., 3.)])},
               times={1: deque([120.])}, completed={1: [[(0., 1., 2.)]]},
               last_frame=181, last_time=120., last_angle=1., turn_angle=2.,
               compare_since=None, _geometry_revision={1: 4}, _drawn={1: (4, 8)})
    monkeypatch.setitem(tip_tracking._ACTIVE, s.scene.as_pointer(), trail)
    def fail(*args, **kwargs):
        trail.points[1].clear()
        trail.times[1].clear()
        trail.completed[1].clear()
        trail.last_frame = trail.last_time = trail.last_angle = None
        trail.turn_angle = 0.
        trail._geometry_revision[1] = 0
        trail._drawn[1] = None
        s.scene['wfrl_tip_trail_clear_reason'] = 'replay_clear'
        raise RuntimeError('frame unavailable')
    s.flex.update = fail
    with pytest.raises(RuntimeError, match='frame unavailable'):
        s.set_time(121.)
    assert list(trail.points[1]) == [(1., 2., 3.)]
    assert list(trail.times[1]) == [120.]
    assert trail.completed[1] == [[(0., 1., 2.)]]
    assert (trail.last_frame, trail.last_time, trail.last_angle, trail.turn_angle) == (181, 120., 1., 2.)
    assert trail._geometry_revision == {1: 4} and trail._drawn == {1: (4, 8)}
    assert 'wfrl_tip_trail_clear_reason' not in s.scene


def test_set_time_success_commits_requested_time_and_all_turbine_update(timed_editor):
    r, s = timed_editor, timed_editor.session
    visible = s.flex.visible_turbines
    def update(scene, *, sim_time_s, record_telemetry):
        assert scene is s.scene and sim_time_s == 121.125
        assert record_telemetry is False and r.namespace._UPDATING
        assert s.flex.visible_turbines == {0, 1}
    s.flex.update = Mock(side_effect=update)
    s.set_time(121.125)
    assert (s.scene.frame_current, s.scene.frame_subframe) == (248, .5)
    assert s.scene['defect_time_s'] == 121.125
    assert s.scene['defect_analysis_status'] == 'NOT_EVALUATED' and s.records is None
    assert s.flex.visible_turbines is visible and not r.namespace._UPDATING


def test_set_time_accepts_native_axis_angle_array_without_copy_protocol(timed_editor):
    class RNAArray:
        def __iter__(self):
            return iter([0., 0., 1., 0.])

        def __copy__(self):
            raise TypeError('RNA array cannot be copied')

    r, s = timed_editor, timed_editor.session
    r.objects[0].rotation_axis_angle = RNAArray()
    s.flex.update = Mock()
    s.set_time(121.)
    assert s.scene['defect_time_s'] == 121.
    assert list(r.objects[0].rotation_axis_angle) == [0., 0., 1., 0.]


@pytest.mark.parametrize('time_s', [float('nan'), float('inf'), 116., 178.])
def test_set_time_invalid_time_does_not_mutate_display(timed_editor, time_s):
    r, s = timed_editor, timed_editor.session
    s.flex.update = Mock()
    with pytest.raises(ValueError, match='时间线'):
        s.set_time(time_s)
    s.scene.frame_set.assert_not_called()
    s.flex.update.assert_not_called()
    assert (s.scene.frame_current, s.scene.frame_subframe) == (181, .25)
    assert not r.namespace._UPDATING
