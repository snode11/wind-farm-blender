"""Endpoint probes must use declared inversion and independently recompute error."""
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.lidar import audit_dual_beam_accuracy as audit
from wfrl.lidar.dual_beam import reconstruct


def echo(point):
    origin = np.array([0., 0., 100.])
    delta = np.asarray(point)-origin
    return dict(time_s=1., origin_m=origin.tolist(), direction=(delta/np.linalg.norm(delta)).tolist(),
                slant_range_m=float(np.linalg.norm(delta)), first_object='blade', blade_id=1,
                observed=True, valid=True, reason='valid')


def test_declared_method_and_error_are_preserved_in_endpoint_probes(monkeypatch):
    config = dict(schema='wfrl.dual-beam-calibration.v1', status='DIAGNOSTIC',
                  origins_m=[[0,0,100]]*3, angles_deg=[10,12,14], common_origin_approximation=True,
                  range_m=[5.,100.], min_point_separation_m=.25, min_orientation_projection_m=.01,
                  effective_length_m=63., measurement_hold_s=1., alarm_hold_s=1.,
                  expected_window_half_angle_deg=15., reconstruction_method='hub-tls.v1')
    obs = dict(S2=echo([-8.,0.,70.]), S3=echo([-7.,0.,40.]))
    clearance = lambda point: abs(point[0])-3.
    pair = reconstruct(obs['S2'],obs['S3'],[-8,0,90],63.,clearance,time_s=1.,method='hub-tls.v1')
    source = SimpleNamespace(nacelles=np.array([np.column_stack((np.eye(3),np.zeros(3)))]),
        tower_transforms=np.array([[np.column_stack((np.eye(3),np.zeros(3)))]]),
        tower_station=np.array([0,0,0]), tower_reference=np.array([[0.,0.,0.]]*3),
        tower_triangles=np.array([[0,1,2]]),layout=np.zeros(3))
    data = dict(samples=[dict(time_s=1.,observations=obs,reconstruction=pair,
        evaluation=dict(tip_reference_m=[-9.,0.,27.],clearance_reference_m=6.,clearance_error_m=999.))])
    calls = []
    def spy(*args,**kwargs):
        calls.append(kwargs['method'])
        return reconstruct(*args,**kwargs)
    monkeypatch.setattr(audit,'reconstruct',spy)
    monkeypatch.setattr(audit,'horizontal_clearance',lambda p,*args:(clearance(p),None))
    result = audit.accuracy_audit(source,data,config)
    assert calls == ['hub-tls.v1']*16
    assert result['bias_m'] == pytest.approx(pair['clearance_estimate']-6.)
    assert result['endpoint_error_statistics']['error_samples'] == 16
    assert result['endpoint_reasons'] == {'valid':16}
    assert len(result['fixed_pair_sensor_sensitivity'][0]['probes']) == 16
    assert result['perturbation_definition']['coordinate_frame'].endswith('+y')
    assert 'estimated_direction_to_reference_chord_deg' in result
    assert 'secant_to_reference_chord_deg' not in result


def test_empty_accuracy_is_unavailable():
    config = dict(schema='wfrl.dual-beam-calibration.v1',status='DIAGNOSTIC',
        origins_m=[[0,0,100]]*3,angles_deg=[10,12,14],common_origin_approximation=True,
        range_m=[5.,100.],min_point_separation_m=.25,min_orientation_projection_m=.01,
        effective_length_m=63.,measurement_hold_s=1.,alarm_hold_s=1.,expected_window_half_angle_deg=15.)
    result = audit.accuracy_audit(None,dict(samples=[]),config)
    assert result['mae_m'] is None
    assert result['endpoint_error_statistics']['min_signed_error_m'] is None
