"""UA provenance, explicit selection, immutable defaults and dimensional A3 limits."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pytest

from scripts.experiments.derive_polar_consistent_ua import BUNDLE, SOURCE, FIELDS, derive, generate
from scripts.experiments.compare_dynamic_stall import convergence
from wfcrl.fastfarm_physics import configure_physics, normalize_physics
from wfcrl.simul_utils import FASTInputFile, create_ff_case
from wfrl.physics_options import (add_physics_arguments, apply_scene_physics,
                                 physics_arguments, physics_cli_arguments, validate_physics)
from wfrl.scene.schema import dump_scene, from_dict


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def case(path, physics):
    return create_ff_case(dict(num_turbines=1, xcoords=[0.], ycoords=[0.], max_iter=15,
        dt=3, speed=8, wind_time_series=None, physics=physics), str(path))


def test_polar_bundle_reproduces_and_preserves_every_nonparameter_byte():
    manifest = generate(check=True)
    for name, record in manifest['files'].items():
        original, derived = (SOURCE/name).read_bytes(), (BUNDLE/name).read_bytes()
        assert re.findall(rb'\r?\n', original) == re.findall(rb'\r?\n', derived)
        def strip_parameters(raw):
            for key in FIELDS:
                raw = re.sub(rb'(?m)^([ \t]*)[^ \t\r\n]+([ \t]+'+key.encode()+rb'\b)', rb'\1VALUE\2', raw)
            return raw
        assert strip_parameters(original) == strip_parameters(derived)
        assert np.array_equal(FASTInputFile(str(SOURCE/name))['AFCoeff'],
                              FASTInputFile(str(BUNDLE/name))['AFCoeff'])
        if record['nonlifting_section']:
            assert derived == original
        else:
            assert record['parameters']['alpha2'] < record['parameters']['alpha0'] < record['parameters']['alpha1']
            assert record['f_at_stall'] == pytest.approx([.7, .7], abs=1e-10)


def test_derivation_refuses_to_write_inside_the_template():
    with pytest.raises(ValueError, match='outside the immutable template'):
        generate(output=SOURCE.parent/'unsafe-polar-output')


def test_symmetric_polar_recovers_anchor_and_opposite_stall_points():
    alpha = np.arange(-180., 180.01, .5)
    cl = np.clip(2*np.pi*np.deg2rad(alpha), -1.2, 1.2)
    table = np.column_stack((alpha, cl, np.full_like(alpha, .01), np.full_like(alpha, -.05)))
    values, diagnostic = derive(table)
    assert values['alpha0'] == pytest.approx(0., abs=1e-10)
    assert values['C_nalpha'] == pytest.approx(2*np.pi, rel=.01)
    assert values['alpha1'] == pytest.approx(-values['alpha2'], rel=.01)
    assert values['Cn1'] == pytest.approx(-values['Cn2'], rel=.01)
    assert values['Cd0'] == .01 and values['Cm0'] == -.05
    assert diagnostic['slope_search_deg'] == [-20., 20.]


@pytest.mark.parametrize('selection', [None, True, 1, [], {}, 'auto', 'OFFICIAL'])
def test_invalid_parameter_choices_are_rejected(selection):
    options = dict(dynamic_stall=True, dynamic_stall_parameters=selection)
    for validate in (validate_physics, normalize_physics):
        with pytest.raises(ValueError, match='official or polar_consistent'):
            validate(options)


def test_nondefault_parameters_require_enabled_ua_before_case_creation(tmp_path):
    with pytest.raises(ValueError, match='requires dynamic_stall'):
        validate_physics(dict(dynamic_stall=False, dynamic_stall_parameters='polar_consistent'))
    for options in ({'dynamic_stall_parameters': 'polar_consistent'},
                    dict(dynamic_stall=False, dynamic_stall_parameters='polar_consistent')):
        with pytest.raises(ValueError, match='requires dynamic_stall'):
            case(tmp_path/'invalid', options)
        assert not (tmp_path/'invalid').exists()


@pytest.mark.parametrize('enabled', [False, True])
def test_official_selection_is_byte_identical_to_implicit_default(tmp_path, enabled):
    a, b = tmp_path/'implicit', tmp_path/'official'
    case(a, {'dynamic_stall': enabled})
    case(b, dict(dynamic_stall=enabled, dynamic_stall_parameters='official'))
    assert hashes(a) == hashes(b)
    assert not list(a.rglob('*polar_consistent*'))


def test_selected_case_references_derived_inputs_and_binds_their_hashes(tmp_path):
    before = hashes(SOURCE)
    options = dict(dynamic_stall=True, dynamic_stall_parameters='polar_consistent')
    farm = case(tmp_path, options)
    record = configure_physics(farm, options)
    assert record['requested']['dynamic_stall_parameters'] == 'polar_consistent'
    assert record['turbines'][0]['actual']['AFAeroMod'] == 2
    assert record['turbines'][0]['checks']['ua_parameter_set']['manifest_sha256'] == hashlib.sha256((BUNDLE/'manifest.json').read_bytes()).hexdigest()
    for entry in record['turbines'][0]['checks']['airfoils']:
        assert entry['file'].endswith('_polar_consistent.dat')
        assert record['inputs_sha256'][entry['file']] == hashlib.sha256((tmp_path/entry['file']).read_bytes()).hexdigest()
    assert hashes(SOURCE) == before
    after = hashes(tmp_path)
    assert configure_physics(farm, options, apply=True) == record
    assert hashes(tmp_path) == after
    with pytest.raises(ValueError, match='explicit polar_consistent'):
        configure_physics(farm, {'dynamic_stall': True})


def test_unrecognized_polar_rejected_without_partial_writes(tmp_path):
    farm = case(tmp_path, {})
    source = tmp_path/'5MW_Baseline/Airfoils/DU25_A17.dat'
    source.write_bytes(source.read_bytes()+b'\r\n')
    before = hashes(tmp_path)
    with pytest.raises(ValueError, match='Official polar input hash mismatch'):
        configure_physics(farm, dict(dynamic_stall=True, dynamic_stall_parameters='polar_consistent'), apply=True)
    assert hashes(tmp_path) == before


def test_corrupted_derived_file_is_rejected_without_repairing_it(tmp_path):
    options = dict(dynamic_stall=True, dynamic_stall_parameters='polar_consistent')
    farm = case(tmp_path, options)
    path = tmp_path/'5MW_Baseline/Airfoils/DU25_A17_polar_consistent.dat'
    path.write_bytes(path.read_bytes().replace(b'7.77228648931', b'6.77228648931'))
    before = hashes(tmp_path)
    with pytest.raises(ValueError, match='Derived case airfoil hash mismatch'):
        configure_physics(farm, options, apply=True)
    assert hashes(tmp_path) == before


def test_cli_selection_and_scene_override_preserve_ua_enablement():
    parser = argparse.ArgumentParser()
    add_physics_arguments(parser)
    args = parser.parse_args(['--dynamic-stall-parameters', 'polar_consistent'])
    scene = from_dict({'layout': [{'id': 'T1', 'x': 0, 'y': 0}], 'physics': {'dynamic_stall': True}})
    assert physics_cli_arguments(args) == ['--dynamic-stall-parameters', 'polar_consistent']
    apply_scene_physics(scene, physics_arguments(args))
    assert scene.make_case(15).dict()['physics'] == dict(dynamic_stall=True, dynamic_stall_parameters='polar_consistent')
    apply_scene_physics(scene, {'dynamic_stall_parameters': 'official'})
    assert scene.physics == {'dynamic_stall': True}


def test_training_identity_omits_default_and_separates_nondefault_with_or_without_scene(tmp_path):
    from scripts.train.train_fastfarm import experiment_config
    base = dict(dynamic_stall=True)
    official = dict(**base, dynamic_stall_parameters='official')
    derived = dict(**base, dynamic_stall_parameters='polar_consistent')
    configs = [experiment_config('Dec_Turb3_Row1_Fastfarm', 2, 4, physics=value)
               for value in (base, official, derived)]
    assert configs[0] == configs[1] != configs[2]
    assert configs[2]['physics']['dynamic_stall_parameters'] == 'polar_consistent'
    configs = []
    path = tmp_path/'scene.yaml'
    for value in (base, official, derived):
        dump_scene(from_dict({'layout': [{'id': 'T1', 'x': 0, 'y': 0}], 'physics': value}), path)
        configs.append(experiment_config('unused', 2, 4, scene=str(path)))
    assert configs[0] == configs[1] != configs[2]
    assert 'dynamic_stall_parameters' not in configs[0]['scene']['physics']


def test_a3_absolute_tolerance_handles_small_fluctuations_without_hiding_large_ones():
    reference = dict(mean=1000., rms=1000., std=.006, peak_abs=1000.01, del_1hz=.02)
    current = {**reference, 'std': .008, 'del_1hz': .025}
    result = convergence(current, reference)
    assert not result['std']['original_relative_pass']
    assert result['std']['suggested_pass'] and result['del_1hz']['suggested_pass']
    assert result['std']['relative_percent'] == pytest.approx(100/3)
    assert not convergence({**current, 'std': 1.}, reference)['std']['suggested_pass']
    assert not convergence({**current, 'mean': 1020.}, reference)['mean']['suggested_pass']
    zero = dict.fromkeys(reference, 0.)
    assert convergence(zero, zero)['std']['relative_percent'] is None
    assert not convergence({**zero, 'std': .001}, zero)['std']['suggested_pass']
