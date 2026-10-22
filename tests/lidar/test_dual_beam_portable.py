from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from wfrl.lidar.dual_beam_replay import METHOD_ALGORITHM_VERSIONS, digest, resolve_package
from wfrl.lidar.dual_beam_package import portable_copy


def fixture_package(tmp_path):
    source=tmp_path/'original-source';source.mkdir()
    (source/'data.json').write_text('{}')
    sm=dict(schema='wfrl.farm-flex-review.v3',turbine_ids=['T1','T2','T3'],segment={'start_s':1,'end_s':2},files={'data.json':digest(source/'data.json')})
    (source/'manifest.json').write_text(json.dumps(sm))
    package=tmp_path/'original-result';package.mkdir()
    config=dict(schema='wfrl.dual-beam-calibration.v1',status='SIMULATION_SELECTED',alarm_hold_s=1,measurement_hold_s=1,effective_length_m=63,origins_m=[[0,0,90]]*3,angles_deg=[10,12.05,14.09])
    (package/'calibration.json').write_text(json.dumps(config))
    (package/'results.json').write_text(json.dumps({tid:{} for tid in sm['turbine_ids']}))
    manifest=dict(schema='wfrl.dual-beam-review.v1',status='REVIEW_ONLY',sample_kind='source',source_package='../original-source',
        source_hashes={'manifest.json':digest(source/'manifest.json'),**sm['files']},segment=sm['segment'],
        files={name:digest(package/name) for name in ('calibration.json','results.json')})
    (package/'manifest.json').write_text(json.dumps(manifest))
    return package,source


def test_relocation_has_no_dependency_on_original_directories(tmp_path):
    package,source=fixture_package(tmp_path)
    portable=portable_copy(package,tmp_path/'portable')
    moved=tmp_path/'moved';shutil.move(portable,moved)
    shutil.rmtree(package);shutil.rmtree(source)
    geometry,overlay=resolve_package(moved)
    assert geometry==moved/'source'
    assert overlay['manifest']['portable']
    (geometry/'data.json').write_text('corrupted')
    with pytest.raises(ValueError,match='integrity'):resolve_package(moved)


def test_incomplete_source_inventory_is_rejected(tmp_path):
    package,_=fixture_package(tmp_path)
    m=json.loads((package/'manifest.json').read_text());del m['source_hashes']['data.json']
    (package/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='inventory'):resolve_package(package)


def test_result_traversal_and_rejected_algorithm_are_rejected(tmp_path):
    package,_=fixture_package(tmp_path)
    m=json.loads((package/'manifest.json').read_text());m['files']['../other']='invalid'
    (package/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='integrity'):resolve_package(package)


def write_algorithm_contract(package, method):
    config = json.loads((package / 'calibration.json').read_text())
    manifest = json.loads((package / 'manifest.json').read_text())
    version = METHOD_ALGORITHM_VERSIONS[method]
    config.update(reconstruction_method=method, algorithm_version=version)
    manifest.update(reconstruction_method=method, algorithm_version=version)
    results = {tid: {'samples': [dict(reconstruction=dict(valid=False, method=method,
                                                          algorithm_version=version))]}
               for tid in ('T1', 'T2', 'T3')}
    return config, manifest, results


def save_algorithm_contract(package, config, manifest, results):
    for name, value in (('calibration.json', config), ('results.json', results)):
        (package / name).write_text(json.dumps(value))
        manifest['files'][name] = digest(package / name)
    (package / 'manifest.json').write_text(json.dumps(manifest))


@pytest.mark.parametrize('method', list(METHOD_ALGORITHM_VERSIONS))
def test_explicit_algorithm_package_is_resolvable_and_portable(tmp_path, method):
    package, _ = fixture_package(tmp_path)
    save_algorithm_contract(package, *write_algorithm_contract(package, method))
    portable = portable_copy(package, tmp_path / 'portable')
    _, overlay = resolve_package(portable)
    assert overlay['config']['reconstruction_method'] == method
    assert overlay['manifest']['algorithm_version'] == METHOD_ALGORITHM_VERSIONS[method]


@pytest.mark.parametrize('location,field,value', [
    ('config', 'reconstruction_method', 'unknown.v1'),
    ('manifest', 'reconstruction_method', 'unknown.v1'),
    ('row', 'method', 'unknown.v1'),
    ('config', 'algorithm_version', 'two-point-hub-extrapolation.v1'),
    ('manifest', 'algorithm_version', 'two-point-hub-extrapolation.v1'),
    ('row', 'algorithm_version', 'two-point-hub-extrapolation.v1'),
    ('config', 'reconstruction_method', None),
    ('manifest', 'reconstruction_method', None),
    ('row', 'method', None),
    ('config', 'algorithm_version', None),
    ('manifest', 'algorithm_version', None),
])
def test_resolver_rejects_unknown_missing_and_mixed_tls_identifiers(tmp_path, location, field, value):
    package, _ = fixture_package(tmp_path)
    config, manifest, results = write_algorithm_contract(package, 'hub-tls.v1')
    metadata = (results['T3']['samples'][0]['reconstruction'] if location == 'row'
                else config if location == 'config' else manifest)
    if value is None:
        del metadata[field]
    else:
        metadata[field] = value
    # Recompute file hashes so semantic rejection cannot be mistaken for integrity rejection.
    save_algorithm_contract(package, config, manifest, results)
    with pytest.raises(ValueError, match='method|version'):
        resolve_package(package)


def test_legacy_absent_method_cannot_claim_tls_version(tmp_path):
    package, _ = fixture_package(tmp_path)
    manifest = json.loads((package / 'manifest.json').read_text())
    manifest['algorithm_version'] = 'hub-constrained-tls.v1'
    (package / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='version'):
        resolve_package(package)


def test_sidecar_reader_loads_without_numpy_or_wfrl_package_imports(tmp_path):
    module = Path(__file__).resolve().parents[2] / 'wfrl/lidar/dual_beam_replay.py'
    script = """import runpy, sys
sys.modules['numpy'] = None
sys.modules['wfrl'] = None
reader = runpy.run_path(sys.argv[1])
assert reader['METHOD_ALGORITHM_VERSIONS']['hub-tls.v1'] == 'hub-constrained-tls.v1'
"""
    subprocess.run([sys.executable, '-I', '-c', script, str(module)], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
