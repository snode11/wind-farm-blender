"""Frozen-frame analysis retries temporary failures without sampling a new frame."""
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender.radar_x100 import runtime, scene as scene_api
from wfrl.radar_x100 import schema, tracking


@pytest.mark.parametrize('change_roi', [False, True])
def test_analysis_recovers_on_the_same_frame_with_bounded_retry(monkeypatch, change_roi):
    session = runtime.Session.__new__(runtime.Session)
    session.context = SimpleNamespace(identity=('frozen-context',))
    session.frame = SimpleNamespace(xyz=np.array([[1., 0., 0.]]))
    session.analysis_key = session.analysis_result = session.side_query = None
    session.analysis_retry_key = None
    session.analysis_retry_at = 0.
    session.analysis_error = ''
    clock, calls = [0.], []
    result = {'distance': np.array([1.])}

    class Query:
        def __init__(self, context):
            assert context is session.context

        def query(self, points, roi):
            calls.append(roi)
            if len(calls) == 1:
                raise RuntimeError('temporary query failure')
            return result

    monkeypatch.setattr(scene_api, 'SideWallQuery', Query)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock[0])
    roi = ((-2., -2., -2.), (2., 2., 2.))
    frame, context = session.frame, session.context
    session.derive_analysis(roi)
    assert session.analysis_result is None and session.analysis_error
    session.derive_analysis(roi)
    assert len(calls) == 1
    if change_roi:
        roi = ((-3., -3., -3.), (3., 3., 3.))
    else:
        clock[0] = 1.
    session.derive_analysis(roi)
    assert session.analysis_result is result and not session.analysis_error
    assert session.frame is frame and session.context is context
    session.derive_analysis(roi)
    assert len(calls) == 2


@pytest.mark.parametrize('retry', ['deadline', 'switch', 'toggle'])
def test_blade_failure_retries_and_successful_empty_fit_is_cached(monkeypatch, retry):
    session = runtime.Session.__new__(runtime.Session)
    session.scene = SimpleNamespace(wfrl_x100_blade_tracking=True)
    session.generation, session.source_session, session.mode = 'generation', 'source', 'SIMULATION'
    session.frame_index, session.display_valid = None, True
    session.blade_tracker = tracking.BladeTracker()
    matrix = np.eye(4)
    priors = [tracking.BladePrior(blade, matrix, 60.) for blade in (1, 2, 3)]
    session.sampler = SimpleNamespace(source_id='source', geometry_id='geometry', calibration_id='calibration',
        analysis_context=lambda frame, identity: SimpleNamespace(identity=identity, world_from_sensor=matrix),
        blade_priors=lambda time_s: priors)
    session.frame = schema.RadarFrame('X100', 'source', 0, 'GEOMETRY_SIMULATION', 'GEOMETRY',
        np.zeros(0, dtype=schema.POINT_DTYPE), time=schema.FrameTime(source_time_s=0.))
    session.pair_context(session.frame)
    clock, calls = [0.], []

    class Query:
        def __init__(self, context):
            assert context is session.context

        def query(self, points, roi):
            calls.append(session.context.identity)
            if len(calls) == 1:
                raise RuntimeError('temporary tower query failure')
            return {'distance': np.zeros(len(points))}

    monkeypatch.setattr(scene_api, 'SideWallQuery', Query)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock[0])
    session.derive_blades()
    assert session.blade_result is session.blade_key is None and session.blade_error
    session.derive_blades()
    assert len(calls) == 1
    if retry == 'deadline':
        clock[0] = .25
    elif retry == 'switch':
        session.source_session = 'other source'
        session.pair_context(session.frame)
    else:
        session.scene.wfrl_x100_blade_tracking = False
        session.derive_blades()
        session.scene.wfrl_x100_blade_tracking = True
    session.derive_blades()
    assert len(calls) == 2 and not session.blade_error
    assert all(row['status'] == 'INSUFFICIENT_POINTS' for row in session.blade_result['blades'])
    assert session.blade_key == session.context.identity
    session.derive_blades()
    assert len(calls) == 2
