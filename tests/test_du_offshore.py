"""OC3 model provenance, opt-in identity, defaults and native offshore telemetry."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfcrl.fastfarm_physics import configure_physics, PHYSICS_KEYS
from wfcrl.fastfarm_substructure import TEMPLATE, PLATFORM_DOFS, normalize_substructure, reference_manifest
from wfcrl.simul_utils import FASTInputFile, create_ff_case
from wfrl.scene.schema import from_dict, dump_scene, load_scene, SceneError
from wfrl.solver_outputs import SolverOutputs, prepare_case
from wfrl.blender_bridge.messages import SOLVER_OUTPUT_CHANNELS, TOWER_OUTPUT_CHANNELS, OFFSHORE_OUTPUT_CHANNELS

SEA = dict(model='oc3_monopile', hs_m=2., tp_s=8., direction_deg=0., seed=123456789)
PHYSICS = dict(tower_flex=True)


def case_args(**extra):
    return dict(num_turbines=1, xcoords=[0.], ycoords=[0.], max_iter=15, dt=3,
                speed=8, wind_time_series=None, **extra)


def scene(**extra):
    return from_dict(dict(layout=[dict(id='T1', x=0., y=0.)], **extra))


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def make(root, **changes):
    return create_ff_case(case_args(physics=PHYSICS, substructure={**SEA, **changes}), str(root))


def test_offshore_disabled_preserves_exact_default_case_and_prepared_bytes(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    first = create_ff_case(case_args(), str(a))
    second = create_ff_case(case_args(physics=dict.fromkeys(PHYSICS_KEYS, False), substructure=None), str(b))
    expected = json.loads((Path(__file__).parent / 'fixtures/dq/default_case_sha256.json').read_text())
    for stage in ('create', 'prepared'):
        if stage == 'prepared':
            prepare_case(first)
            prepare_case(second, dict.fromkeys(PHYSICS_KEYS, False), substructure=None)
        assert hashes(a) == hashes(b)
        assert {key: hashes(a)[key] for key in expected[stage]} == expected[stage]
    assert 'substructure' not in scene().to_dict()
    assert 'substructure' not in scene().make_case(40).dict()


@pytest.mark.parametrize('key,value', [('hs_m', -1), ('hs_m', 6.01), ('hs_m', True), ('hs_m', float('nan')),
    ('tp_s', 0), ('tp_s', 21), ('tp_s', '8'), ('direction_deg', -180), ('direction_deg', 181), ('direction_deg', float('inf')),
    ('seed', 2**31), ('seed', -(2**31)-1), ('seed', 1.5), ('seed', True), ('water_depth_m', 21),
    ('model', 'floating'), ('unknown', 1)])
def test_invalid_sea_states_rejected_before_generation(tmp_path, key, value):
    options = {**SEA, key: value}
    with pytest.raises(ValueError, match='substructure'):
        create_ff_case(case_args(physics=PHYSICS, substructure=options), str(tmp_path / 'case'))
    assert not (tmp_path / 'case').exists()
    with pytest.raises(SceneError, match='substructure'):
        scene(physics=PHYSICS, substructure=options)


def test_valid_sea_state_boundaries_and_very_large_numbers():
    for seed in (-(2**31), 2**31 - 1):
        assert normalize_substructure({**SEA, 'seed': seed, 'direction_deg': 180.})['seed'] == seed
    with pytest.raises(ValueError, match='hs_m'):
        normalize_substructure({**SEA, 'hs_m': 10**1000})


@pytest.mark.parametrize('options', [{}, [], True, {'model': 'oc3_monopile'}])
def test_incomplete_substructure_rejected(options):
    with pytest.raises(ValueError, match='substructure'):
        normalize_substructure(options)


@pytest.mark.parametrize('physics,direction', [({}, 270), ({'tower_flex': False}, 270),
    ({'tower_flex': True, 'blade_torsion': True}, 270), (PHYSICS, 240)])
def test_unsupported_combinations_rejected_without_writes(tmp_path, physics, direction):
    with pytest.raises(ValueError, match='OC3'):
        create_ff_case(case_args(physics=physics, substructure=SEA, direction=direction), str(tmp_path / 'case'))
    assert not (tmp_path / 'case').exists()


def test_official_inputs_hashes_complete_structure_and_individual_modules(tmp_path):
    manifest = reference_manifest()
    assert manifest['repository'] == 'https://github.com/OpenFAST/r-test'
    assert manifest['tag'] == 'v3.5.3'
    for item in manifest['files']:
        assert item['url'].startswith('https://github.com/OpenFAST/r-test/blob/' + manifest['commit'] + '/')
        content = (TEMPLATE / item['file']).read_bytes()
        assert b'\n' not in content.replace(b'\r\n', b'')
        assert hashlib.sha256(content).hexdigest() == item['stored_sha256']
    args = case_args(physics=PHYSICS, substructure=SEA)
    args.update(num_turbines=3, xcoords=[0., 504., 1008.], ycoords=[0., 0., 0.])
    f = create_ff_case(args, str(tmp_path))
    defs = prepare_case(f, PHYSICS, substructure=SEA)
    record = configure_physics(f, PHYSICS, substructure=SEA)
    assert all(d['offshore_outputs'] for d in defs)
    assert int(FASTInputFile(f)['Mod_WaveField']) == 2
    hydro_files = []
    for i in range(1, 4):
        primary_path = tmp_path / f'FarmInputs/FFTest_WT{i}.fst'
        primary = FASTInputFile(str(primary_path))
        assert [int(primary[k]) for k in ('CompHydro', 'CompSub', 'CompMooring')] == [1, 1, 0]
        assert primary['DT'] == .005 and primary['WtrDpth'] == 20 and primary['WtrDens'] == 1027
        ed = FASTInputFile(str((primary_path.parent / primary['EDFile'].strip('"')).resolve()))
        assert all(ed[k] for k in PLATFORM_DOFS)
        assert ed['TowerBsHt'] == ed['PtfmRefzt'] == 10
        assert 'OC3Monopile' in ed['TwrFile']
        assert 'PtfmHeave' in ' '.join(ed['OutList'])
        hd_path = (primary_path.parent / primary['HydroFile'].strip('"')).resolve()
        hydro_files.append(hd_path)
        hd = FASTInputFile(str(hd_path))
        assert [hd[k] for k in ('WaveMod', 'WaveHs', 'WaveTp', 'WaveDir', 'WaveSeed(1)')] == [2, 2., 8., 0., 123456789]
        dll = tmp_path / f'5MW_Baseline/ServoData/DISCON_WT{i}.dll'
        original_dll = TEMPLATE.parent.parent / 'servo_dll/DISCON_WT1.dll'
        assert dll.read_bytes() == original_dll.read_bytes()
    assert len(set(hydro_files)) == 3
    for name, digest in record['inputs_sha256'].items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
    assert json.loads(Path(f).with_suffix('.physics.json').read_text()) == record
    before = hashes(tmp_path)
    prepare_case(f, PHYSICS, substructure=SEA)
    assert hashes(tmp_path) == before


@pytest.mark.parametrize('field,value', [('hs_m', 3.), ('tp_s', 9.), ('direction_deg', 90.), ('seed', 10)])
def test_sea_state_is_part_of_portable_case_identity(tmp_path, field, value):
    a, b = make(tmp_path / 'a'), make(tmp_path / 'b', **{field: value})
    assert configure_physics(a, PHYSICS, substructure=SEA)['identity'] != configure_physics(
        b, PHYSICS, substructure={**SEA, field: value})['identity']
    before = hashes(tmp_path)
    with pytest.raises(ValueError, match='does not match'):
        configure_physics(b, PHYSICS, substructure=SEA)
    assert hashes(tmp_path) == before


def test_invalid_reference_is_rejected_before_any_physics_writes(tmp_path):
    farm = make(tmp_path)
    path = tmp_path / 'OC3/WT1/NRELOffshrBsline5MW_OC3Monopile_HydroDyn.dat'
    content = path.read_bytes().replace(b'             0   PotMod', b'             1   PotMod')
    path.write_bytes(content)
    before = hashes(tmp_path)
    with pytest.raises(ValueError, match='reference parameter'):
        configure_physics(farm, PHYSICS, substructure={**SEA, 'hs_m': 3.}, apply=True)
    assert hashes(tmp_path) == before


def test_scene_roundtrip_bridge_overrides_and_training_identity(tmp_path):
    from wfrl.blender_bridge.workflow import configured_scene
    from scripts.train.train_fastfarm import experiment_config
    source = scene(physics=PHYSICS, substructure=SEA)
    path = tmp_path / 'scene.yaml'
    dump_scene(source, path)
    assert load_scene(path).substructure == normalize_substructure(SEA)
    assert load_scene(path).make_case(40).dict()['substructure'] == normalize_substructure(SEA)
    first = experiment_config('unused', 1, 2, scene=str(path))
    changed = configured_scene(source, {'substructure': {**SEA, 'seed': 10}})
    dump_scene(changed, path)
    second = experiment_config('unused', 1, 2, scene=str(path))
    assert first != second and first['scene']['substructure']['seed'] == SEA['seed']
    assert configured_scene(source, {'substructure': None}).substructure is None
    with pytest.raises(SceneError):
        scene(backend='floris', physics=PHYSICS, substructure=SEA)


def test_driver_reads_scene_case_substructure_and_rejects_direction_override(tmp_path):
    from wfcrl import environments  # noqa: F401
    from wfcrl.interface import FastFarmInterface
    from wfrl.fastfarm_driver import FastFarmDriver
    farm = make(tmp_path)
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.physics = PHYSICS
    driver.env = NS(mdp=NS(interface=FastFarmInterface(1, farm, max_iter=40),
                           farm_case=scene(physics=PHYSICS, substructure=SEA).make_case(40)))
    driver._prepare_solver_outputs()
    assert driver._solver_outputs.definitions[0]['offshore_outputs']
    assert driver.physics_record['substructure']['requested'] == normalize_substructure(SEA)
    with pytest.raises(ValueError, match='wind_direction=270'):
        driver.reset(wind_direction=240)
    with pytest.raises(ValueError, match='explicit substructure'):
        configure_physics(farm, PHYSICS)


def test_native_offshore_units_sign_timestamps_missing_and_invalid(tmp_path):
    farm = make(tmp_path)
    outputs = SolverOutputs(farm, PHYSICS, substructure=SEA)
    outputs.start({'farm_file': {'path': farm}})
    catalog = {**SOLVER_OUTPUT_CHANNELS, **TOWER_OUTPUT_CHANNELS, **OFFSHORE_OUTPUT_CHANNELS}
    names = ['Time', *[s['native'] for s in catalog.values()]]
    units = ['(s)', *['(' + s['native_unit'] + ')' for s in catalog.values()]]
    values = {key: (-1234.5 if key == 'mudline_moment_y' else .15) for key in catalog}
    output = outputs.readers[0].path
    output.write_text(' '.join(names) + '\n' + ' '.join(units) + '\n' +
                      ' '.join(map(str, [2.75, *values.values()])) + '\n')
    data, records = outputs.sample(3., 3.)
    assert data['mudline_moment_y'][0] == -1234.5
    for key in OFFSHORE_OUTPUT_CHANNELS:
        r = records[0][key]
        assert r['value'] == values[key] and r['validity'] == 'valid'
        assert r['timestamp']['value'] == 2.75
        assert r['unit'] == OFFSHORE_OUTPUT_CHANNELS[key]['unit']
    _, records = outputs.sample(9., 3.)
    assert records[0]['wave_elevation']['validity'] == 'waiting'
    assert records[0]['wave_elevation']['value'] is None
    output.write_text(output.read_text().replace('(N*m)', '(kN-m)'))
    fresh = SolverOutputs(farm, PHYSICS, substructure=SEA)
    fresh.start({'farm_file': {'path': farm}})
    _, records = fresh.sample(3., 3.)
    assert records[0]['mudline_moment_y']['validity'] == 'invalid'


def test_offshore_comparison_rejects_incomplete_and_unit_mismatch(tmp_path):
    from scripts.experiments.compare_fastfarm_offshore import native_table, summarize
    path = tmp_path / 'test.out'
    path.write_text('Time GenPwr\n(s) (kW)\n0 1\n60 2\n')
    with pytest.raises(ValueError, match='did not finish'):
        native_table(path, 120)
    path.write_text('Time GenPwr TwrBsMxt TwrBsMyt TTDspFA TTDspSS\n(s) (MW) (kN-m) (kN-m) (m) (m)\n120 1 2 3 4 5\n')
    with pytest.raises(ValueError, match='expected kW'):
        summarize(path, 120, False)
