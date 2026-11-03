"""Frozen-frame analysis retries temporary failures without sampling a new frame."""
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender.radar_x100 import runtime, scene as scene_api


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
