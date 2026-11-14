"""Independent physics switches, immutable defaults and native displacement contracts."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import pytest

from wfcrl.fastfarm_physics import PHYSICS_KEYS, TOWER_DOFS, configure_physics
from wfcrl.simul_utils import FASTInputFile, create_ff_case
from wfrl.physics_options import add_physics_arguments, physics_arguments, apply_scene_physics
from wfrl.scene.schema import SceneError, from_dict, dump_scene, load_scene
from wfrl.solver_outputs import prepare_case, SolverOutputs
from wfrl.blender_bridge.messages import ALL_SOLVER_OUTPUT_CHANNELS, TOWER_OUTPUT_CHANNELS


def case_args(**extra):
    return dict(num_turbines=1, xcoords=[0.], ycoords=[0.], max_iter=15,
                dt=3, speed=8, wind_time_series=None, **extra)


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()}


def scene_data(**extra):
    return dict(layout=[dict(id='T1', x=0, y=0)], **extra)


def test_disabled_case_is_byte_identical_to_default_and_fixed_input_contract(tmp_path):
    first, second = tmp_path / 'default', tmp_path / 'disabled'
    a = create_ff_case(case_args(), str(first))
    b = create_ff_case(case_args(physics=dict.fromkeys(PHYSICS_KEYS, False)), str(second))
    expected = json.loads((Path(__file__).parent / 'fixtures/dq/default_case_sha256.json').read_text(encoding='utf-8'))
    for stage in ('create', 'prepared'):
        if stage == 'prepared':
            prepare_case(a)
            prepare_case(b, dict.fromkeys(PHYSICS_KEYS, False))
        assert hashes(first) == hashes(second)
        assert {name: hashes(first)[name] for name in expected[stage]} == expected[stage]
    before = hashes(first)
    record = configure_physics(a)
    assert record['requested'] == dict.fromkeys(PHYSICS_KEYS, False)
    assert hashes(first) == before
    assert not Path(a).with_suffix('.physics.json').exists()


@pytest.mark.parametrize('bits', list(itertools.product((False, True), repeat=3)))
def test_switches_are_independent_and_case_identity_matches_inputs(tmp_path, bits):
    options = dict(zip(PHYSICS_KEYS, bits))
    farm = create_ff_case(case_args(physics=options), str(tmp_path))
    definitions = prepare_case(farm, options)
    record = configure_physics(farm, options)
    actual = record['turbines'][0]['actual']
    assert [actual[k] for k in TOWER_DOFS] == [options['tower_flex']] * 4
    assert actual['AFAeroMod'] == (2 if options['dynamic_stall'] else 1)
    assert actual['UAMod'] == 3
    assert actual['TwrPotent'] == actual['TwrShadow'] == int(options['tower_shadow'])
    assert actual['TwrAero'] is False
    assert bool(definitions[0].get('tower_outputs')) == options['tower_flex']
    primary = FASTInputFile(str(tmp_path / 'FarmInputs/FFTest_WT1.fst'))
    ed = FASTInputFile(str((tmp_path / 'FarmInputs' / primary['EDFile'].strip('"')).resolve()))
    outlist = ' '.join(ed['OutList'])
    assert all(name in outlist for name in ('TwrBsMxt', 'TwrBsMyt'))
    assert ('TTDspFA' in outlist and 'TTDspSS' in outlist) == options['tower_flex']
    if any(bits):
        saved = json.loads(Path(farm).with_suffix('.physics.json').read_text(encoding='utf-8'))
        assert saved == record
        for name, digest in saved['inputs_sha256'].items():
            assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
    checks = record['turbines'][0]['checks']
    if options['dynamic_stall']:
        assert len(checks['airfoils']) == 8
        assert sum(a['nonlifting_section'] for a in checks['airfoils']) == 2
    if options['tower_shadow']:
        assert checks['tower_nodes'] == dict(count=12, elevation_m=[0., 87.6])
    before = hashes(tmp_path)
    prepare_case(farm, options)
    assert hashes(tmp_path) == before


@pytest.mark.parametrize('physics', [None, [], True, {'typo': True}, {'tower_flex': 1},
                                    {'dynamic_stall': 'true'}, {'tower_shadow': None}])
def test_scene_rejects_unknown_or_nonboolean_physics(physics):
    with pytest.raises(SceneError, match='physics'):
        from_dict(scene_data(physics=physics))


def test_scene_and_cli_preserve_independent_options_and_reject_floris(tmp_path):
    scene = from_dict(scene_data(physics=dict(tower_flex=True, dynamic_stall=True)))
    path = tmp_path / 'scene.yaml'
    dump_scene(scene, path)
    assert load_scene(path).physics == scene.physics
    parser = argparse.ArgumentParser()
    add_physics_arguments(parser)
    assert physics_arguments(parser.parse_args([])) == {}
    options = physics_arguments(parser.parse_args(['--no-dynamic-stall', '--tower-shadow']))
    updated = apply_scene_physics(load_scene(path), options)
    assert updated.physics == dict(tower_flex=True, dynamic_stall=False, tower_shadow=True)
    case = updated.make_case(15)
    assert case.dict()['physics'] == updated.physics
    assert 'physics' not in from_dict(scene_data()).make_case(15).dict()
    with pytest.raises(SceneError, match='fastfarm'):
        from_dict(scene_data(backend='floris', physics={'tower_flex': True}))


@pytest.mark.parametrize('problem', ['missing_ua', 'ua_disabled', 'wrong_uamod', 'tower_diameter',
                                    'tower_elevation', 'external_input'])
def test_invalid_physics_data_rejected_before_any_input_write(tmp_path, problem):
    farm = create_ff_case(case_args(), str(tmp_path / 'case'))
    root = Path(farm).parent.parent
    ad_path = root / '5MW_Baseline/AD.dat'
    ad = FASTInputFile(str(ad_path))
    options = dict.fromkeys(PHYSICS_KEYS, True)
    if problem in ('missing_ua', 'ua_disabled'):
        af_path = root / '5MW_Baseline/Airfoils/DU21_A17.dat'
        if problem == 'missing_ua':
            content = af_path.read_text(encoding='utf-8')
            af_path.write_text('\n'.join(line for line in content.splitlines() if 'T_f0' not in line) + '\n', encoding='utf-8')
        else:
            af = FASTInputFile(str(af_path))
            af['InclUAdata'] = False
            af.write(str(af_path))
    elif problem == 'wrong_uamod':
        ad['UAMod'] = 2
        ad.write(str(ad_path))
    elif problem.startswith('tower_'):
        table = ad['TowProp'].copy()
        if problem == 'tower_diameter':
            table[0, 1] = 0
        else:
            table[1, 0] = table[0, 0]
        ad['TowProp'] = table
        ad.write(str(ad_path))
    else:
        ad['AFNames'][0] = '"../../../external.dat"'
        ad.write(str(ad_path))
    before = hashes(root)
    with pytest.raises((ValueError, KeyError)):
        configure_physics(farm, options, apply=True)
    assert hashes(root) == before


def test_tower_displacement_uses_native_units_timestamp_and_missing_status(tmp_path):
    farm = create_ff_case(case_args(), str(tmp_path))
    outputs = SolverOutputs(farm, {'tower_flex': True})
    outputs.start({'farm_file': {'path': farm}})
    specs = ALL_SOLVER_OUTPUT_CHANNELS
    names = ['Time', *(s['native'] for s in specs.values())]
    units = ['(s)', *('(' + s['native_unit'] + ')' for s in specs.values())]
    numbers = [2.75, *(0.125 if key == 'tower_top_fa' else -0.02 if key == 'tower_top_ss' else 1. for key in specs)]
    outputs.readers[0].path.write_text(' '.join(names) + '\n' + ' '.join(units) + '\n' +
                                     ' '.join(map(str, numbers)) + '\n', encoding='utf-8')
    values, records = outputs.sample(3., 3.)
    np.testing.assert_equal(values['tower_top_fa'], [0.125])
    np.testing.assert_equal(values['tower_top_ss'], [-0.02])
    for key in TOWER_OUTPUT_CHANNELS:
        assert records[0][key]['unit'] == 'm'
        assert records[0][key]['timestamp']['value'] == 2.75
        assert records[0][key]['validity'] == 'valid'
    _, stale = outputs.sample(9., 3.)
    assert stale[0]['tower_top_fa']['value'] is None
    assert stale[0]['tower_top_fa']['validity'] == 'waiting'


def test_training_identity_preserves_defaults_and_separates_physics(tmp_path):
    from scripts.train.train_fastfarm import experiment_config
    default = experiment_config('Dec_Turb3_Row1_Fastfarm', 2, 4)
    disabled = experiment_config('Dec_Turb3_Row1_Fastfarm', 2, 4, physics=dict.fromkeys(PHYSICS_KEYS, False))
    enabled = experiment_config('Dec_Turb3_Row1_Fastfarm', 2, 4, physics={'tower_flex': True})
    assert default == disabled
    assert enabled != default
    assert enabled['physics'] == dict(tower_flex=True, dynamic_stall=False, tower_shadow=False)
    path = tmp_path / 'scene.yaml'
    dump_scene(from_dict(scene_data(physics={'tower_flex': True})), path)
    overridden = experiment_config('unused', 2, 4, scene=str(path), physics={'tower_flex': False})
    assert 'physics' not in overridden and 'physics' not in overridden['scene']


def test_driver_prepares_physics_identity_from_actual_inputs(tmp_path):
    from types import SimpleNamespace
    from wfcrl import environments  # noqa: F401
    from wfcrl.interface import FastFarmInterface
    from wfrl.fastfarm_driver import FastFarmDriver
    farm = create_ff_case(case_args(), str(tmp_path))
    interface = FastFarmInterface(1, farm, max_iter=15)
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.env = SimpleNamespace(mdp=SimpleNamespace(interface=interface))
    driver.physics = {'tower_flex': True, 'dynamic_stall': True}
    driver._prepare_solver_outputs()
    assert driver._solver_outputs.definitions[0]['tower_outputs']
    assert driver.physics_record == configure_physics(farm, driver.physics)
    assert driver.physics_record['turbines'][0]['actual']['AFAeroMod'] == 2


def test_comparison_rejects_incomplete_output_and_reports_native_units(tmp_path):
    from scripts.experiments.compare_fastfarm_physics import summarize
    path = tmp_path / 'Case.T1.out'
    path.write_text('Time GenPwr RootMyc1 RootMxc1 TwrBsMxt TwrBsMyt TTDspFA TTDspSS\n'
                    '(s) (kW) (kN-m) (kN-m) (kN-m) (kN-m) (m) (m)\n'
                    '15 1000 10 2 4 5 .1 -.02\n30 1200 12 4 6 7 .2 -.03\n', encoding='utf-8')
    report = summarize(path, 30)
    assert report['channels']['GenPwr'] == dict(unit='kW', mean=1100., std=100., min=1000., max=1200., peak_abs=1200.)
    assert report['channels']['TTDspSS']['peak_abs'] == .03
    with pytest.raises(RuntimeError, match='Incomplete'):
        summarize(path, 45)


def test_bridge_physics_survives_scene_reload_and_request_overrides(tmp_path):
    from wfrl.blender_bridge.backend_session import BackendSession
    from wfrl.blender_bridge.workflow import configured_scene
    source = from_dict(scene_data(physics={'dynamic_stall': True}))
    path = tmp_path / 'scene.yaml'
    dump_scene(source, path)
    session = BackendSession(physics={'tower_flex': True})
    session.command({'command': 'scene.load', 'arguments': {'scene': str(path)}})
    assert session.scene.physics == dict(dynamic_stall=True, tower_flex=True)
    assert session._load(path).physics == session.scene.physics
    request = configured_scene(session.scene, {'physics': {'dynamic_stall': False, 'tower_shadow': True}})
    assert request.physics == dict(dynamic_stall=False, tower_flex=True, tower_shadow=True)
    assert source.physics == {'dynamic_stall': True}
    with pytest.raises(ValueError, match='physics'):
        configured_scene(session.scene, {'physics': {'dynamic_stall': 1}})


def test_three_turbines_use_same_physics_and_preserve_tower_output_channels(tmp_path):
    options = dict.fromkeys(PHYSICS_KEYS, True)
    args = case_args(physics=options)
    args.update(num_turbines=3, xcoords=[0., 504., 1008.], ycoords=[0., 0., 0.])
    farm = create_ff_case(args, str(tmp_path))
    definitions = prepare_case(farm, options)
    record = configure_physics(farm, options)
    assert len(record['turbines']) == len(definitions) == 3
    assert all(d['tower_outputs'] for d in definitions)
    assert all(t['actual'] == record['turbines'][0]['actual'] for t in record['turbines'])


def test_formal_bridge_handoff_serializes_effective_physics(tmp_path, monkeypatch):
    from wfrl.blender_bridge.backend_session import BackendSession
    source = from_dict(scene_data())
    path = tmp_path / 'scene.yaml'
    dump_scene(source, path)
    session = BackendSession(physics={'tower_flex': True})
    captured = []

    def capture(scene_path, options):
        captured.append(load_scene(scene_path).physics)
        raise RuntimeError('stop before process launch')

    monkeypatch.setattr(session, '_formal_command', capture)
    with pytest.raises(RuntimeError, match='stop before process launch'):
        session.start('formal_training', {'scene': str(path),
                      'scene_overrides': {'physics': {'dynamic_stall': True}}})
    assert captured == [dict(tower_flex=True, dynamic_stall=True)]
    assert load_scene(path).physics == {}


def test_driver_persists_physics_in_episode_metadata(tmp_path, monkeypatch):
    import collections
    from types import SimpleNamespace
    from wfcrl import environments  # noqa: F401
    from wfcrl.interface import FastFarmInterface
    from wfrl.fastfarm_driver import FastFarmDriver
    farm = create_ff_case(case_args(), str(tmp_path))
    interface = FastFarmInterface(1, farm, max_iter=15)
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.physics_record = configure_physics(farm)
    driver._solver_outputs = None
    driver.wind_time_series, driver.hub_height = None, 90.
    driver.max_steps, driver.dt = 15, 3.
    driver._yaw_win = collections.deque()
    saved = []
    monkeypatch.setattr(interface, '_save_wind_record', lambda: saved.append(dict(interface.wind_record)))
    monkeypatch.setattr(interface, 'mark_wind_control_start', lambda: None)
    driver.env = SimpleNamespace(mdp=SimpleNamespace(interface=interface),
        reset=lambda **kwargs: setattr(interface, 'wind_record', {'status': 'running'}))
    driver._has_solver = lambda: False
    driver._episode_ended = lambda: False
    driver._freestream = lambda **kwargs: 8.
    driver._read = lambda **kwargs: {}
    driver.reset(wind_speed=8.)
    assert saved[0]['physics'] == driver.physics_record
    assert saved[0]['physics'] is not driver.physics_record
