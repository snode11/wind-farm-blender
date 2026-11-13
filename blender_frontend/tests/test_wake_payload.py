import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender.wake import (WakeFrameBuffer, WakePayloadError, grid_mesh,
                               validate_wake_payload)


def test_wake_age_uses_source_age_and_local_monotonic_time():
    data = channel({'kind': 'disxy', 'shape': [2, 2], 'values': [1, 2, 3, 4],
                    'x': [0, 1], 'y': [0, 1]})
    data['source_age_seconds'] = 1.5
    frame = validate_wake_payload({'encoding': 'json', 'turbine_ids': ['T1'],
                                   'data': data}, received_monotonic=10.)
    assert frame.validity_at(13.49) == 'valid'
    assert frame.validity_at(13.5) == 'stale'
    assert frame.validity_at(10., connected=False) == 'stale'
    assert frame.source_age_seconds == 1.5


@pytest.mark.parametrize('validity', ['invalid', 'waiting', 'stale', 'unsupported'])
def test_nonvalid_wake_keeps_reason_and_cannot_become_current(validity):
    data = channel(None)
    data.update(validity=validity, error='source unavailable')
    frame = validate_wake_payload({'encoding': 'json', 'kind': 'disxy',
        'turbine_ids': ['T1'], 'data': data}, received_monotonic=10.)
    assert frame.validity_at(10.) == validity
    assert not frame.is_renderable
    assert frame.error == 'source unavailable'


def channel(value, *, fidelity="SYNTH", provenance=None):
    return {
        "value": value, "unit": "m/s", "validity": "valid", "error": None,
        "fidelity": fidelity,
        "provenance": provenance or {"formula": "deterministic test"},
        "source_age_seconds": 0.0, "stale_after_seconds": 5.0,
    }


@pytest.mark.parametrize(('shape', 'dimensions'), [
    pytest.param([2.9, 3.7], (2, 3), id='fractional'),
    pytest.param(['2', '3'], (2, 3), id='strings'),
    pytest.param([True, 6], (1, 6), id='boolean'),
    pytest.param([None, 6], (1, 6), id='none'),
    pytest.param([0, 6], (0, 6), id='zero'),
])
def test_wake_shape_rejects_invalid_dimensions(shape, dimensions):
    ny, nx = dimensions
    payload = {'encoding': 'json', 'turbine_ids': ['T1'], 'data': channel({
        'kind': 'disxy', 'shape': shape, 'values': [1.0] * (ny * nx),
        'x': list(range(nx)), 'y': list(range(ny)),
    })}
    with pytest.raises(WakePayloadError):
        validate_wake_payload(payload)


@pytest.mark.parametrize('shape', [
    pytest.param([2, 3], id='integers'),
    pytest.param([2.0, 3.0], id='integral-floats'),
])
def test_wake_shape_accepts_integer_dimensions(shape):
    payload = {'encoding': 'json', 'turbine_ids': ['T1'], 'data': channel({
        'kind': 'disxy', 'shape': shape, 'values': [1.0] * 6,
        'x': [0, 1, 2], 'y': [0, 1],
    })}
    frame = validate_wake_payload(payload)
    assert frame.shape == (2, 3)
    assert all(type(item) is int for item in frame.shape)
    vertices, faces = grid_mesh(frame)
    assert len(vertices) == 6
    assert len(faces) == 2


def test_disxy_material_failure_keeps_mesh_and_reports_error_once(monkeypatch, capsys):
    from wfrl_blender import materials, runtime, wake
    from wfrl_blender.state import FrontendState

    class Scene(dict):
        pass

    class WakeObject(dict):
        hide_render = False
        hidden = False

        def __init__(self, name, mesh):
            super().__init__()
            self.name, self.data = name, mesh

        def hide_get(self):
            return self.hidden

        def hide_set(self, hidden):
            self.hidden = hidden

    def new_mesh(name):
        mesh = NS(name=name, materials=[], update=Mock())

        def from_pydata(vertices, edges, faces):
            mesh.vertices = [NS(co=co) for co in vertices]
            mesh.polygons = list(faces)

        mesh.from_pydata = Mock(side_effect=from_pydata)
        return mesh

    scene = Scene()
    scene.objects = {}
    link = Mock(side_effect=lambda obj: scene.objects.__setitem__(obj.name, obj))
    scene.collection = NS(name='WFRL_Scene', children_recursive=[], objects=NS(link=link))
    bpy = NS(context=NS(scene=scene), data=NS(
        materials=[],
        meshes=NS(new=Mock(side_effect=new_mesh)),
        objects=NS(get=scene.objects.get, new=Mock(side_effect=WakeObject))))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    state = FrontendState(mode='replay', connection='CONNECTED', run_status='RUNNING')
    monkeypatch.setattr(runtime, '_state', state)
    monkeypatch.setattr(wake, '_material_failures', set())
    monkeypatch.setattr(wake.time, 'monotonic', lambda: 10.)
    get_material = Mock()
    monkeypatch.setattr(materials, 'get_material', get_material)
    frame = validate_wake_payload({'encoding': 'json', 'turbine_ids': ['T1'],
        'data': channel({'kind': 'disxy', 'shape': [2, 2], 'values': [1, 2, 3, 4],
                         'x': [0, 1], 'y': [0, 1]})}, sequence=1, received_monotonic=10.)
    vertices, faces = grid_mesh(frame)

    for error, expected_tracebacks in (
            (RuntimeError('wake shader unavailable'), 1),
            (RuntimeError('wake shader unavailable'), 0),
            (ValueError('wake shader unavailable'), 1),
            (RuntimeError('wake shader node missing'), 1)):
        scene.objects.clear()
        state.error = ''
        get_material.reset_mock()
        get_material.side_effect = error

        obj = wake.apply_disxy_frame(frame, scene=scene)

        assert obj is scene.objects['WFRL.WakeDisXY']
        link.assert_called_with(obj)
        bpy.data.objects.new.assert_called_with('WFRL.WakeDisXY', obj.data)
        obj.data.from_pydata.assert_called_once_with(vertices, [], faces)
        assert obj.data.materials == []
        assert state.error == f'DisXY wake material failed: {error}'
        assert state.connection == 'CONNECTED' and state.confirmed
        assert scene['wfrl_disxy_validity'] == obj['validity'] == 'valid'
        assert not obj.hidden and not obj.hide_render

        for sequence in (2, 3):
            assert wake.apply_disxy_frame(replace(frame, sequence=sequence), scene=scene) is obj
            assert obj['sequence'] == sequence
        get_material.assert_called_once_with('wake')
        assert state.error == f'DisXY wake material failed: {error}'
        stderr = capsys.readouterr().err
        assert stderr.count('Traceback (most recent call last):') == expected_tracebacks
        if expected_tracebacks:
            assert f'{type(error).__name__}: {error}' in stderr
        else:
            assert stderr == ''


class WakePayloadTests(unittest.TestCase):
    def test_proxy_payload_preserves_synth_semantics(self):
        payload = {
            "encoding": "json", "turbine_ids": ["T1"],
            "timestamp": {"value": 2.0, "timebase": "simulation_seconds"},
            "data": channel({"kind": "floris_proxy", "shape": [2, 2],
                              "values": [8.0, 7.5, 7.0, 8.0], "x": [0, 10], "y": [0, 10], "z": 90.0}),
        }
        frame = validate_wake_payload(payload, sequence=4)
        self.assertEqual((frame.kind, frame.fidelity, frame.sequence), ("floris_proxy", "SYNTH", 4))
        self.assertTrue(frame.is_renderable)

    def test_disxy_grid_order_and_topology(self):
        payload = {
            "encoding": "json", "turbine_ids": ["T1", "T2"],
            "data": channel({"kind": "disxy", "fidelity": "EXPORTED",
                              "provenance": {"file": "case.Low.DisXY01.001.vtk"},
                              "shape": [2, 3], "values": [1, 2, 3, 4, 5, 6],
                              "x": [0, 10, 20], "y": [4, 14], "z": 90}),
        }
        frame = validate_wake_payload(payload)
        vertices, faces = grid_mesh(frame)
        self.assertEqual(vertices[0], (0.0, 4.0, 90.0))
        self.assertEqual(vertices[-1], (20.0, 14.0, 90.0))
        self.assertEqual(faces, ((0, 1, 4, 3), (1, 2, 5, 4)))

    def test_rejects_shape_mismatch_and_coalesces_old_frames(self):
        bad = {"encoding": "json", "turbine_ids": ["T1"], "data": channel({
            "kind": "disxy", "shape": [2, 2], "values": [1, 2, 3]})}
        with self.assertRaises(WakePayloadError):
            validate_wake_payload(bad)
        buf = WakeFrameBuffer()
        frame = validate_wake_payload({"encoding": "json", "turbine_ids": ["T1"],
            "data": channel({"shape": [1], "values": [8]})}, sequence=2)
        newer = validate_wake_payload({"encoding": "json", "turbine_ids": ["T1"],
            "data": channel({"shape": [1], "values": [7]})}, sequence=3)
        self.assertTrue(buf.push(frame)); self.assertTrue(buf.push(newer))
        self.assertEqual(buf.pop_latest().sequence, 3)

if __name__ == "__main__":
    unittest.main()



class WakePresentationMotionTests(__import__('unittest').TestCase):
    def test_ring_advects_downstream_and_retains_circular_cross_section(self):
        from wfrl_blender.wake import proxy_ring_points_local, wake_section
        a = list(proxy_ring_points_local(3, 96, 0))
        b = list(proxy_ring_points_local(3, 96, .5))
        self.assertGreater(b[0][0], a[0][0])
        cy, cz, radius = wake_section(3.5 / 22, .5)
        self.assertAlmostEqual(sum(p[2] for p in b) / len(b), cz, places=7)
        self.assertAlmostEqual(sum(p[1] for p in b) / len(b), cy, places=7)
        for p in b:
            self.assertAlmostEqual(p[0], b[0][0])
            self.assertAlmostEqual((p[1]-cy)**2+(p[2]-cz)**2, radius**2)

    def test_no_phase_boundary_jump_and_bounded_meander(self):
        from wfrl_blender.wake import proxy_ring_points_local, wake_section, proxy_pulse_points
        a = list(proxy_ring_points_local(4, 96, .999))
        b = list(proxy_ring_points_local(4, 96, 1.001))
        self.assertGreater(b[0][0],a[0][0])
        self.assertLess(b[0][0]-a[0][0],.1)
        for phase in (0,1,10,22,100):
            for u in (0,.25,.5,.75,1):
                cy,cz,r=wake_section(u,phase)
                self.assertLessEqual(abs(cy),4)
                self.assertLessEqual(abs(cz),2)
                self.assertLess(r,72)
            p=list(proxy_pulse_points(2,10,phase))
            self.assertTrue(all(a[0]<=b[0] for a,b in zip(p,p[1:])))
