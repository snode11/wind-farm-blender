"""Release assets really load two algorithms without changing S1 or B2 data."""
import importlib.util
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from wfrl_blender import farm_flex


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def builtins():
    return {method: farm_flex.read_package(path) for method, path in (
        ('hub-axis.v1', farm_flex.default_dual_package()),
        ('hub-tls.v1', farm_flex.default_dual_tls_package()))}


@pytest.mark.parametrize('method,version', [('hub-axis.v1', 'two-point-hub-extrapolation.v1'),
                                          ('hub-tls.v1', 'hub-constrained-tls.v1')])
def test_builtins_load_actual_method_samples_and_pending_status(builtins, method, version):
    manifest, _, _, _, readers = builtins[method]
    assert manifest['dual_beam']['reconstruction_method'] == method
    assert manifest['dual_beam']['algorithm_version'] == version
    assert manifest['dual_beam']['status'] == 'REVIEW_ONLY'
    for reader in readers.values():
        assert reader.reconstruction_method == method
        assert reader.algorithm_version == version
        row = next(row for row in reader.rows if row['reconstruction']['valid'])
        value = reader.at(row['time_s'])
        assert value['reconstruction_method'] == method
        assert value['algorithm_version'] == version
        assert value['performance_status'] == 'PENDING_ACCEPTANCE'
        assert value['measurement']['estimate_m'] == row['reconstruction']['clearance_estimate']
        assert value['measurement']['truth_m'] == row['evaluation']['clearance_reference_m']
        assert value['measurement']['time_s'] == row['time_s']


def test_switching_algorithm_keeps_source_geometry_and_s1_independent(builtins):
    axis, tls = builtins['hub-axis.v1'], builtins['hub-tls.v1']
    for index in (1, 2, 3):
        assert np.array_equal(axis[index], tls[index])
    assert axis[0]['dual_beam']['source_hashes'] == tls[0]['dual_beam']['source_hashes']
    different_estimates = 0
    for tid in ('T1', 'T2', 'T3'):
        a, b = axis[-1][tid], tls[-1][tid]
        assert a.events == b.events
        assert len(a.rows) == len(b.rows)
        for original, candidate in zip(a.rows, b.rows):
            assert original['observations']['S1'] == candidate['observations']['S1']
            assert original['s1_observation_state'] == candidate['s1_observation_state']
            assert original['observations']['S2'] == candidate['observations']['S2']
            assert original['observations']['S3'] == candidate['observations']['S3']
            different_estimates += (original['reconstruction']['clearance_estimate']
                                   != candidate['reconstruction']['clearance_estimate'])
    assert different_estimates > 0, 'TLS must read its own saved estimates, not relabel old results'


def test_original_b2_package_remains_available():
    manifest, _, _, _, readers = farm_flex.read_package(farm_flex.default_package())
    assert 'measurement_mode' not in manifest
    for reader in readers.values():
        value = reader.at(reader.end_s)
        assert value.get('measurement_mode') is None
        assert reader.package.measurements


@pytest.mark.parametrize('package', [farm_flex.default_package(), farm_flex.default_dual_package(),
                                    farm_flex.default_dual_tls_package()])
def test_saved_builtin_relocation_uses_manifest_identity(tmp_path, package):
    scene = dict(wfrl_farm_flex_path=str(tmp_path / 'removed-install' / package.name),
                 wfrl_farm_manifest_sha256=hashlib.sha256((package / 'manifest.json').read_bytes()).hexdigest())
    assert farm_flex.saved_package(scene) == package


def test_saved_builtin_cannot_substitute_another_method(tmp_path):
    missing = tmp_path / 'removed-install' / 'dual_beam_tls'
    scene = dict(wfrl_farm_flex_path=str(missing), wfrl_farm_manifest_sha256='wrong-manifest')
    assert farm_flex.saved_package(scene) == missing
    # An existing path still reaches the caller's strict hash check, rather
    # than silently replacing a modified external result with an internal one.
    scene['wfrl_farm_flex_path'] = str(farm_flex.default_dual_package())
    scene['wfrl_farm_manifest_sha256'] = hashlib.sha256((farm_flex.default_dual_tls_package() / 'manifest.json').read_bytes()).hexdigest()
    assert farm_flex.saved_package(scene) == farm_flex.default_dual_package()


@pytest.fixture(scope='module')
def builder():
    spec = importlib.util.spec_from_file_location('dual_builtin_builder', ROOT / 'scripts/blender/build_extension.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def payloads(builder):
    return builder._payloads()


def test_builder_checks_both_sidecars_against_bundled_source(builder, payloads):
    builder._validate_farm_payloads(payloads)
    bundled = payloads
    for method, directory, package in (
        ('hub-axis.v1', 'dual_beam', farm_flex.default_dual_package()),
        ('hub-tls.v1', 'dual_beam_tls', farm_flex.default_dual_tls_package())):
        bundled = builder._bundle_dual(bundled, package, directory, method)
        manifest = json.loads(dict(bundled)['assets/' + directory + '/manifest.json'])
        assert manifest['reconstruction_method'] == method
        assert manifest['source_package'] == '../mappo'
        assert manifest['portable'] is True
    assert dict(bundled)['assets/dual_beam/results.json'] != dict(bundled)['assets/dual_beam_tls/results.json']


@pytest.mark.parametrize('method,package', [('hub-axis.v1', farm_flex.default_dual_tls_package()),
                                          ('hub-tls.v1', farm_flex.default_dual_package())])
def test_builder_rejects_swapped_method_assets(builder, payloads, method, package):
    with pytest.raises(ValueError, match='method differs from entry point'):
        builder._bundle_dual(payloads, package, 'entry-point', method)


def test_builder_rejects_different_bundled_geometry(builder, payloads):
    mutated = [(name, b'changed source' if name == 'assets/mappo/geometry.npz' else data)
               for name, data in payloads]
    with pytest.raises(ValueError, match='Farm package integrity'):
        builder._validate_farm_payloads(mutated)
    with pytest.raises(ValueError, match='differs from bundled geometry'):
        builder._bundle_dual(mutated, farm_flex.default_dual_tls_package(), 'dual_beam_tls', 'hub-tls.v1')
