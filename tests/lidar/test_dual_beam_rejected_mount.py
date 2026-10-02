"""Rejected mounting evidence must not reach geometry processing or replay."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts.lidar import postprocess_dual_beam
from wfrl.lidar.dual_beam import validate_calibration, validate_inner_origins
from wfrl.lidar.dual_beam_replay import SCHEMA, digest, resolve_package

CONFIG = Path(__file__).resolve().parents[2] / 'configs/lidar/dual-beam-diagnostic.json'


@pytest.fixture
def diagnostic():
    return json.loads(CONFIG.read_text())


@pytest.mark.parametrize('status', ('REJECTED', 'REJECTED_OUTSIDE_INNER_MOUNT',
                                    'REJECTED_GEOMETRIC_COLLISION'))
def test_rejected_calibration_fails_before_missing_geometry(tmp_path, diagnostic, status):
    config = deepcopy(diagnostic)
    config['status'] = status
    with pytest.raises(ValueError, match='Rejected installation'):
        validate_calibration(config)
    source = SimpleNamespace(path=tmp_path / 'missing-source')
    with pytest.raises(ValueError, match='Rejected installation'):
        postprocess_dual_beam.process(source, config)


def test_rejected_export_writes_no_partial_package(tmp_path, diagnostic, monkeypatch):
    diagnostic['status'] = 'REJECTED_OUTSIDE_INNER_MOUNT'
    calibration = tmp_path / 'rejected.json'
    calibration.write_text(json.dumps(diagnostic))
    source = tmp_path / 'missing-source'
    output = tmp_path / 'must-not-be-created'
    monkeypatch.setattr(sys, 'argv', ['postprocess_dual_beam.py', '--source', str(source),
                                     '--calibration', str(calibration), '--output', str(output)])
    with pytest.raises(ValueError, match='Rejected installation'):
        postprocess_dual_beam.main()
    assert not output.exists()
    assert not source.exists()


def test_rejected_manifest_reports_reason_before_opening_source(tmp_path):
    reason = 'Outside the rotor-to-tower inner mounting region'
    # Source, calibration and results deliberately do not exist. Rejection must
    # win over missing-key/file errors and preserve the recorded reason.
    (tmp_path / 'manifest.json').write_text(json.dumps(
        dict(schema=SCHEMA, status='REJECTED', rejection_reason=reason,
             source_package='missing-source')))
    with pytest.raises(ValueError, match=reason):
        resolve_package(tmp_path)


def make_sidecar(tmp_path, config):
    source = tmp_path / 'source'
    sidecar = tmp_path / 'diagnostic'
    source.mkdir()
    sidecar.mkdir()
    segment = dict(start_s=117., end_s=177.)
    (source / 'manifest.json').write_text(json.dumps(dict(
        schema='wfrl.farm-flex-review.v3', turbine_ids=['T1'], segment=segment, files={})))
    (sidecar / 'calibration.json').write_text(json.dumps(config))
    (sidecar / 'results.json').write_text(json.dumps({'T1': {}}))
    (sidecar / 'manifest.json').write_text(json.dumps(dict(
        schema=SCHEMA, status='REVIEW_ONLY', sample_kind='source', source_package='../source',
        source_hashes={'manifest.json': digest(source / 'manifest.json')}, segment=segment,
        files={name: digest(sidecar / name) for name in ('calibration.json', 'results.json')})))
    return source, sidecar


def test_rejected_calibration_cannot_hide_in_review_only_manifest(tmp_path, diagnostic):
    diagnostic['status'] = 'REJECTED_OUTSIDE_INNER_MOUNT'
    _, sidecar = make_sidecar(tmp_path, diagnostic)
    with pytest.raises(ValueError, match='Rejected installation'):
        resolve_package(sidecar)


def test_old_diagnostic_calibration_and_sidecar_remain_resolvable(tmp_path, diagnostic):
    origins, directions, limits = validate_calibration(diagnostic)
    assert origins.shape == directions.shape == (3, 3)
    assert limits.valid()
    source, sidecar = make_sidecar(tmp_path, diagnostic)
    resolved, overlay = resolve_package(sidecar)
    assert resolved == source.resolve()
    assert overlay['config']['status'] == 'DIAGNOSTIC_INSTALLATION_UNCONFIRMED'
    assert overlay['config'] == diagnostic


@pytest.fixture
def inner_geometry():
    asset = CONFIG.parents[2] / 'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json'
    data = json.loads(asset.read_text())
    scalars, shell = data['scalars'], data['shell']
    tilt = scalars['ShftTilt']
    assert tilt == -5.0
    hub = [scalars['OverHang'] * math.cos(math.radians(tilt)), 0.,
           scalars['TowerHt'] + scalars['Twr2Shft']
           + scalars['OverHang'] * math.sin(math.radians(tilt))]
    center_x = shell['nacelle_x_bias'] * scalars['OverHang']
    extent = [center_x - shell['length'] / 2, center_x + shell['length'] / 2]
    return hub, tilt, extent, shell['width'] / 2


def test_real_inner_calibration_satisfies_mount_region(inner_geometry):
    config = json.loads((CONFIG.parent / 'dual-beam-inner.json').read_text())
    assert config['installation']['region'] == 'INNER_NACELLE'
    validate_inner_origins(config['origins_m'], *inner_geometry)


@pytest.mark.parametrize('placement', ('rotor_front', 'off_nacelle_lateral',
                                      'behind_tower', 'past_front_footprint'))
def test_inner_region_rejects_external_mounts(inner_geometry, placement):
    _, _, extent, half_width = inner_geometry
    point = {
        'rotor_front': [-7.3, -2., 89.6],
        'off_nacelle_lateral': [-2.5, half_width + .01, 87.54756],
        'behind_tower': [.01, 0., 87.54756],
        'past_front_footprint': [extent[0] - .01, 0., 87.54756],
    }[placement]
    with pytest.raises(ValueError, match='inner side between rotor and tower'):
        validate_inner_origins([point], *inner_geometry)


def test_rotor_half_space_uses_actual_negative_shaft_tilt(inner_geometry):
    hub, _, extent, _ = inner_geometry
    x = extent[0] + .01
    # Both points share an admissible x/y footprint. With the actual -5 degree
    # normal toward the nacelle, raising z moves toward the rotor-front side.
    validate_inner_origins([[x, 0., hub[2] - 4.]], *inner_geometry)
    with pytest.raises(ValueError, match='inner side between rotor and tower'):
        validate_inner_origins([[x, 0., hub[2] + 4.]], *inner_geometry)
