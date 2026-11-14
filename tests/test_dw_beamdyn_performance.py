"""BeamDyn profile compatibility and strict numerical acceptance checks."""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from scripts.experiments.benchmark_beamdyn import compare_metrics, prepare


def statistics():
    metrics = {'power': dict(unit='kW', mean=1700., std=.3)}
    for blade in (1, 2, 3):
        for key, unit, mean, std in (('root_flap', 'kN-m', 5500., 330.),
                                     ('root_edge', 'kN-m', 370., 2400.),
                                     ('tip_flap', 'm', 3.1, .26),
                                     ('tip_twist', 'deg', -.13, .17)):
            metrics[f'B{blade}_{key}'] = dict(unit=unit, mean=mean, std=std)
    return [dict(samples=121, window_seconds=[30., 60.], metrics=metrics)]


@pytest.mark.parametrize('key,metric,value', [
    ('power', 'std', .316),
    ('B2_root_edge', 'mean', 374.),
    ('B3_tip_flap', 'std', .274),
    ('B1_tip_twist', 'mean', -.079),
    ('B3_tip_twist', 'std', .188),
])
def test_each_blade_and_each_acceptance_limit_is_required(key, metric, value):
    reference = statistics()
    candidate = deepcopy(reference)
    candidate[0]['metrics'][key][metric] = value
    assert not compare_metrics(candidate, reference)['passed']
    assert compare_metrics(reference, reference)['passed']


def test_torsion_mean_uses_absolute_degrees_and_zero_std_is_not_waived():
    reference = statistics()
    candidate = deepcopy(reference)
    candidate[0]['metrics']['B1_tip_twist']['mean'] += .049
    assert compare_metrics(candidate, reference)['passed']
    reference[0]['metrics']['power']['std'] = 0.
    candidate[0]['metrics']['power']['std'] = 1e-9
    assert not compare_metrics(candidate, reference)['passed']


def test_incompatible_sampling_cannot_pass():
    reference = statistics()
    candidate = deepcopy(reference)
    candidate[0]['samples'] -= 1
    with pytest.raises(ValueError, match='Sampling'):
        compare_metrics(candidate, reference)


def test_experimental_file_is_derived_and_hashes_identify_actual_inputs(tmp_path):
    from wfcrl.simul_utils import FASTInputFile
    root = tmp_path / 'case'
    farm, hashes = prepare(root, {'DT': .0025, 'order_elem': 4}, 60, 1, 8.)
    primary = FASTInputFile(str(farm.with_name('FFTest_WT1.fst')))
    assert primary['DT'] == .0025
    bd_path = (farm.parent / primary['BDBldFile(1)'].strip('"')).resolve()
    assert bd_path.name == 'DW_BeamDyn.dat'
    assert FASTInputFile(str(bd_path))['order_elem'] == 4
    assert hashes[str(bd_path.relative_to(root))] == hashlib.sha256(bd_path.read_bytes()).hexdigest()
    assert not farm.with_suffix('.physics.json').exists()
    assert FASTInputFile(str(root / '5MW_Baseline/NRELOffshrBsline5MW_BeamDyn.dat'))['order_elem'] == 5


@pytest.mark.parametrize('settings', [{'minimal': True}, {'quadrature': 1}])
def test_reduced_output_list_retains_first_required_channel(tmp_path, settings):
    from wfcrl.simul_utils import FASTInputFile
    farm, _ = prepare(tmp_path / 'case', settings, 60, 1, 12.)
    bd = FASTInputFile(str(farm.parent.parent / '5MW_Baseline/DW_BeamDyn.dat'))
    names = {word.strip('"') for line in bd['OutList'] for word in line.replace(',', ' ').split()}
    assert {'RootMxr', 'RootMyr', 'TipTDxr', 'TipTDyr', 'TipRDxr', 'TipRDyr', 'TipRDzr'} <= names


@pytest.mark.parametrize('value', [None, True, 1, '', 'FAST', 'unknown', []])
def test_fidelity_validation_is_strict(value):
    from wfrl.physics_options import validate_physics
    with pytest.raises(ValueError, match='blade_torsion_fidelity'):
        validate_physics({'blade_torsion': True, 'blade_torsion_fidelity': value})


def test_cli_reference_override_survives_forwarding():
    from wfrl.physics_options import add_physics_arguments, physics_arguments, physics_cli_arguments
    parser = argparse.ArgumentParser()
    add_physics_arguments(parser)
    args = parser.parse_args(['--blade-torsion', '--blade-torsion-fidelity', 'reference'])
    assert physics_arguments(args) == {'blade_torsion': True, 'blade_torsion_fidelity': 'reference'}
    assert physics_arguments(parser.parse_args(physics_cli_arguments(args))) == physics_arguments(args)
    assert physics_arguments(parser.parse_args([])) == {}


def test_training_identity_adds_only_nondefault_fidelity(tmp_path):
    from scripts.train.train_fastfarm import experiment_config
    from wfrl.scene.schema import from_dict, dump_scene

    def config(physics=None, **kwargs):
        return experiment_config('Dec_Turb3_Row1_Fastfarm', 2, 4, physics=physics, **kwargs)

    assert config() == config({'blade_torsion': False, 'blade_torsion_fidelity': 'fast'})
    assert config({'tower_flex': True})['physics'] == dict(
        tower_flex=True, dynamic_stall=False, tower_shadow=False)
    enabled = config({'blade_torsion': True})
    assert enabled == config({'blade_torsion': True, 'blade_torsion_fidelity': 'fast'})
    reference = config({'blade_torsion': True, 'blade_torsion_fidelity': 'reference'})
    assert reference['physics'].pop('blade_torsion_fidelity') == 'reference'
    assert reference == enabled
    mixed = config(dict(blade_torsion=True, blade_torsion_fidelity='reference',
                        dynamic_stall=True, dynamic_stall_parameters='polar_consistent'))
    assert mixed['physics']['blade_torsion_fidelity'] == 'reference'
    assert mixed['physics']['dynamic_stall_parameters'] == 'polar_consistent'
    path = tmp_path / 'scene.yaml'
    base = dict(layout=[dict(id='T1', x=0., y=0.)], physics={'blade_torsion': True})
    dump_scene(from_dict(base), path)
    scene_default = config(scene=str(path))
    base['physics']['blade_torsion_fidelity'] = 'fast'
    dump_scene(from_dict(base), path)
    assert config(scene=str(path)) == scene_default
    base['physics']['blade_torsion_fidelity'] = 'reference'
    dump_scene(from_dict(base), path)
    assert config(scene=str(path))['scene']['physics']['blade_torsion_fidelity'] == 'reference'


def test_reference_profile_requires_blade_torsion_for_training():
    from scripts.train.train_fastfarm import experiment_config
    with pytest.raises(ValueError, match='requires blade_torsion'):
        experiment_config('Dec_Turb3_Row1_Fastfarm', 2, 4, physics={'blade_torsion_fidelity': 'reference'})


def test_numerical_profiles_preserve_reference_and_inspect_without_writes(tmp_path):
    from wfcrl.fastfarm_physics import configure_physics
    from wfcrl.simul_utils import FASTInputFile, create_ff_case
    from wfrl.solver_outputs import prepare_case
    from tests.test_dt_blade_torsion import case_args, hashes
    records = {}
    for name, fidelity in [('implicit', None), ('fast', 'fast'), ('reference', 'reference')]:
        physics = {'blade_torsion': True}
        if fidelity is not None:
            physics['blade_torsion_fidelity'] = fidelity
        root = tmp_path / name
        farm = create_ff_case(case_args(physics=physics), str(root))
        prepare_case(farm, physics)
        before = hashes(root)
        records[name] = configure_physics(farm, physics)
        assert hashes(root) == before
        assert records[name]['turbines'][0]['checks']['beamdyn']['fidelity'] == (fidelity or 'fast')
        primary = FASTInputFile(str(Path(farm).with_name('FFTest_WT1.fst')))
        if fidelity == 'reference':
            assert primary['DT'] == .00125
            assert primary['InterpOrder'] == 2
            with pytest.raises(ValueError, match='does not match'):
                configure_physics(farm, {'blade_torsion': True})
            assert hashes(root) == before
        else:
            assert .00125 < primary['DT'] <= .0025
        assert primary['DT_Out'] == .25
    assert records['implicit'] == records['fast']
    assert records['fast']['identity'] != records['reference']['identity']


def test_disabled_default_fidelity_does_not_change_case_bytes(tmp_path):
    from wfcrl.simul_utils import create_ff_case
    from tests.test_dt_blade_torsion import case_args, hashes
    first, second = tmp_path / 'plain', tmp_path / 'fast'
    create_ff_case(case_args(), str(first))
    create_ff_case(case_args(physics={'blade_torsion': False, 'blade_torsion_fidelity': 'fast'}), str(second))
    assert hashes(first) == hashes(second)


@pytest.mark.parametrize('value', [None, True, 1, '', 'FAST', 'unknown', []])
def test_backend_fidelity_validation_rejects_invalid_values(value):
    from wfcrl.fastfarm_physics import normalize_physics
    with pytest.raises(ValueError, match='blade_torsion_fidelity'):
        normalize_physics({'blade_torsion': True, 'blade_torsion_fidelity': value})


def test_profile_manifest_matches_coupling_and_output_intervals():
    import json
    from wfcrl.fastfarm_physics import BEAMDYN_PROFILE_FILE, BEAMDYN_DT, BEAMDYN_REFERENCE_DT
    from wfrl.physics_options import BLADE_TORSION_DEFAULT_FIDELITY
    definition = json.loads(BEAMDYN_PROFILE_FILE.read_bytes())
    assert definition['default'] == BLADE_TORSION_DEFAULT_FIDELITY
    assert BEAMDYN_DT == 1 / 460
    assert BEAMDYN_REFERENCE_DT == .00125
    for profile in definition['profiles'].values():
        assert profile['DT'] * profile['high_interval_substeps'] == pytest.approx(.1)
        assert profile['DT'] * profile['output_interval_substeps'] == pytest.approx(.25)
