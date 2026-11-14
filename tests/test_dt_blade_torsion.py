"""BeamDyn opt-in inputs, finite-rotation torsion and native telemetry contracts."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from wfcrl.fastfarm_physics import BEAMDYN_FILES, BEAMDYN_NODES, BEAMDYN_DT, configure_physics
from wfcrl.simul_utils import FASTInputFile, create_ff_case
from wfrl.blade_torsion import elastic_twist_degrees, rotation_channels
from wfrl.physics_options import add_physics_arguments, physics_arguments
from wfrl.scene.schema import from_dict
from wfrl.solver_outputs import prepare_case, SolverOutputs
from wfrl.blender_bridge.messages import TORSION_OUTPUT_CHANNELS


def case_args(**extra):
    return dict(num_turbines=1, xcoords=[0.], ycoords=[0.], max_iter=20, dt=3,
                speed=8., wind_time_series=None, **extra)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_disabled_matches_complete_preexisting_case_bytes(tmp_path):
    expected = json.loads((Path(__file__).parent / 'fixtures/dt/default_case_sha256.json').read_text())
    for label, options in (('omitted', {}), ('disabled', {'physics': {'blade_torsion': False}})):
        root = tmp_path / label
        farm = create_ff_case(case_args(**options), str(root))
        assert hashes(root) == expected['create']
        prepare_case(farm, options.get('physics'))
        assert hashes(root) == expected['prepared']


@pytest.mark.parametrize('companions', [{}, {'tower_flex': True}, {'dynamic_stall': True},
                                      dict(tower_flex=True, dynamic_stall=True, tower_shadow=True)])
def test_beamdyn_uses_verified_properties_and_six_span_stations(tmp_path, companions):
    physics = dict(blade_torsion=True, **companions)
    farm = create_ff_case(case_args(physics=physics), str(tmp_path))
    definitions = prepare_case(farm, physics)
    record = configure_physics(farm, physics)
    primary = FASTInputFile(str(Path(farm).with_name('FFTest_WT1.fst')))
    bd = FASTInputFile(str(tmp_path / '5MW_Baseline' / BEAMDYN_FILES[0]))
    assert primary['CompElast'] == 2 and primary['DT'] == BEAMDYN_DT
    assert primary['DT_Out'] == .25
    assert bd['QuasiStaticInit'] is True and bd['rhoinf'] == 0 and bd['quadrature'] == 2
    assert bd['order_elem'] == 5 and bd['DTBeam'].strip('"').upper() == 'DEFAULT'
    assert bd['NNodeOuts'] == 6 and tuple(bd['OutNd']) == BEAMDYN_NODES
    names = ' '.join(bd['OutList']).replace(',', ' ').replace('"', '').split()
    assert all(f'N{g}RD{a}r' in names for g in range(1, 7) for a in 'xyz')
    assert definitions[0]['blade_torsion']
    checks = record['turbines'][0]['checks']['beamdyn']
    assert checks['radius_from_root_m'][-1] == 61.5
    assert checks['span_fraction'][0] == 0.
    before = hashes(tmp_path)
    prepare_case(farm, physics)
    assert hashes(tmp_path) == before


@pytest.mark.parametrize('problem', ['section', 'curvature', 'integration', 'coupling', 'geometry'])
def test_rejects_unverified_models_before_writes(tmp_path, problem):
    farm = create_ff_case(case_args(physics={'blade_torsion': True}), str(tmp_path))
    bd_path = tmp_path / '5MW_Baseline' / BEAMDYN_FILES[0]
    bd = FASTInputFile(str(bd_path))
    if problem == 'section':
        path = bd_path.with_name(BEAMDYN_FILES[1])
        path.write_bytes(path.read_bytes().replace(b'9.729480E+08', b'8.729480E+08'))
    elif problem == 'curvature':
        bd['MemberGeom'][5, 0] = .1
        bd.write(str(bd_path))
    elif problem == 'integration':
        bd['rhoinf'] = .5
        bd.write(str(bd_path))
    elif problem == 'coupling':
        data = FASTInputFile(farm)
        data['DT_High'] = .101
        data.write(farm)
    else:
        primary = FASTInputFile(str(Path(farm).with_name('FFTest_WT1.fst')))
        path = (Path(farm).parent / primary['EDFile'].strip('"')).resolve()
        ed = FASTInputFile(str(path))
        ed['TipRad'] = 70.
        ed.write(str(path))
    before = hashes(tmp_path)
    with pytest.raises(ValueError):
        configure_physics(farm, {'blade_torsion': True}, apply=True)
    assert hashes(tmp_path) == before


def test_configuring_existing_driver_case_stages_blade_inputs(tmp_path):
    farm = create_ff_case(case_args(), str(tmp_path))
    assert not (tmp_path / '5MW_Baseline' / BEAMDYN_FILES[0]).exists()
    output = SolverOutputs(farm, {'blade_torsion': True})
    assert output.definitions[0]['comp_elast'] == 2
    assert (tmp_path / '5MW_Baseline' / BEAMDYN_FILES[1]).is_file()


@pytest.mark.parametrize('bend,twist', [(0., 0.), (0., 25.), (55., -12.), (120., 33.)])
def test_swing_twist_recovers_angle_without_bending_contamination(bend, twist):
    swing = Rotation.from_rotvec(np.radians(bend) * np.array([.6, .8, 0.]))
    rotation = swing * Rotation.from_euler('z', twist, degrees=True)
    q = rotation.as_quat()
    c = 4. * q[:3] / (1. + q[3])
    assert elastic_twist_degrees(c) == pytest.approx(twist, abs=1e-10)
    if np.linalg.norm(c):
        shadow = -16. * c / np.dot(c, c)
        assert elastic_twist_degrees(shadow) == pytest.approx(twist, abs=1e-10)


@pytest.mark.parametrize('c', [[4., 0., 0.], [np.nan, 0., 0.], [0., 1.], [np.inf, 0., 0.]])
def test_undefined_or_invalid_rotation_is_not_a_measurement(c):
    with pytest.raises(ValueError):
        elastic_twist_degrees(c)


def test_cli_and_scene_preserve_explicit_blade_torsion():
    parser = argparse.ArgumentParser()
    add_physics_arguments(parser)
    assert physics_arguments(parser.parse_args([])) == {}
    assert physics_arguments(parser.parse_args(['--blade-torsion'])) == {'blade_torsion': True}
    assert physics_arguments(parser.parse_args(['--no-blade-torsion'])) == {'blade_torsion': False}
    scene = from_dict(dict(layout=[dict(id='T1', x=0, y=0)], physics={'blade_torsion': True}))
    assert scene.make_case(20).dict()['physics']['blade_torsion'] is True


def test_native_torsion_requires_full_vector_and_preserves_sample_clock(tmp_path):
    farm = create_ff_case(case_args(), str(tmp_path))
    outputs = SolverOutputs(farm, {'blade_torsion': True})
    outputs.start({'farm_file': {'path': farm}})
    channels = [n for blade in (1, 2, 3) for n in rotation_channels(blade, 6)]
    c = [0., 0., 4. * np.tan(np.radians(3.) / 4.)]
    path = outputs.readers[0].path
    path.write_text('Time ' + ' '.join(channels) + '\n(s) ' + ' '.join(['(-)'] * 9) +
                    '\n2.75 ' + ' '.join(map(str, c * 3)) + '\n', encoding='utf-8')
    values, records = outputs.sample(3., 3.)
    for key in TORSION_OUTPUT_CHANNELS:
        assert values[key][0] == pytest.approx(3.)
        r = records[0][key]
        assert r['unit'] == 'deg' and r['timestamp']['value'] == 2.75
        assert r['provenance']['beamdyn_node'] == 49
        assert len(r['provenance']['channels']) == 3
    _, records = outputs.sample(9., 3.)
    assert records[0]['tip_torsion_1']['validity'] == 'waiting'
    assert records[0]['tip_torsion_1']['value'] is None


def test_missing_transverse_rotation_does_not_use_axial_parameter_as_angle(tmp_path):
    farm = create_ff_case(case_args(), str(tmp_path))
    outputs = SolverOutputs(farm, {'blade_torsion': True})
    outputs.start({'farm_file': {'path': farm}})
    outputs.readers[0].path.write_text('Time B1N6RDzr\n(s) (-)\n3 .03\n', encoding='utf-8')
    values, records = outputs.sample(3., 3.)
    assert np.isnan(values['tip_torsion_1'][0])
    assert records[0]['tip_torsion_1']['validity'] == 'unsupported'


def test_comparison_rotates_blade_vectors_to_the_common_coned_frame(tmp_path):
    from scripts.experiments.compare_fastfarm_physics import summarize
    path = tmp_path / 'Case.T1.out'
    columns = dict(GenPwr=('kW', 1700.), TwrBsMxt=('kN-m', 0.), TwrBsMyt=('kN-m', 0.),
                   TTDspFA=('m', 0.), TTDspSS=('m', 0.), B1RootMxr=('N-m', 1000.),
                   B1RootMyr=('N-m', 2000.), B1TipTDxr=('m', 3.), B1TipTDyr=('m', 4.),
                   BlPitch1=('deg', 30.))
    for blade in (1, 2, 3):
        for gauge in range(1, 7):
            columns.update({name: ('-', 0.) for name in rotation_channels(blade, gauge)})
    path.write_text('Time ' + ' '.join(columns) + '\n(s) ' +
                    ' '.join(f'({unit})' for unit, _ in columns.values()) + '\n' +
                    ''.join(str(t) + ' ' + ' '.join(str(v) for _, v in columns.values()) + '\n'
                            for t in (3., 6.)), encoding='utf-8')
    result = summarize(path, 6, blade_torsion=True)
    common = result['common_channels']
    assert common['root_edge']['mean'] == pytest.approx(np.sqrt(3.) / 2. + 1.)
    assert common['root_flap']['mean'] == pytest.approx(-.5 + np.sqrt(3.))
    assert common['tip_flap_coned']['mean'] == pytest.approx(3. * np.sqrt(3.) / 2. + 2.)
    assert len(result['torsion']) == 18
    assert all(v['mean'] == 0. for v in result['torsion'].values())
    assert path.with_suffix('.torsion.csv').is_file()
