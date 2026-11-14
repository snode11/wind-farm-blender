"""Episode direction, unchanged aligned inputs and world/solver coordinate contracts."""
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from wfcrl import environments, interface, simul_utils
from wfcrl.fastfarm_direction import normalize_direction, rotate_layout, prepare_direction, write_direction
from wfrl.scene import load_scene


def case_args(**kwargs):
    return dict(num_turbines=3, xcoords=[0., 504., 1008.], ycoords=[0., 0., 0.],
                max_iter=3, dt=3, speed=8., wind_time_series=None, **kwargs)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def aligned_reference():
    """Execute the aligned generator without the optional direction integration."""
    source = inspect.getsource(simul_utils.create_ff_case)
    source = source.replace('    from .fastfarm_direction import normalize_direction, prepare_direction, write_direction\n', '')
    source = source.replace('    direction = normalize_direction(case.get("direction", 270.))\n', '')
    # Offshore combination checks take the direction only to reject non-270 inflow.
    source = source.replace(', direction=case.get("direction", 270.))', ')')
    source = source.replace('''    if direction != 270.:
        direction_farm, direction_identity = prepare_direction(outputFSTF, direction)
        write_direction(outputFSTF, direction_farm, direction_identity)
''', '')
    assert 'direction' not in source
    namespace = dict(vars(simul_utils))
    exec(compile(source, '<aligned case reference>', 'exec'), namespace)
    return namespace['create_ff_case']


@pytest.mark.parametrize('selection', [None, '90m_08mps.bts'])
@pytest.mark.parametrize('direction', [None, 270., -90., 630.])
def test_aligned_case_files_are_byte_identical(tmp_path, selection, direction):
    args = case_args()
    args['wind_time_series'] = selection
    aligned_reference()(args, str(tmp_path/'reference'))
    if direction is not None:
        args['direction'] = direction
    simul_utils.create_ff_case(args, str(tmp_path/'actual'))
    assert hashes(tmp_path/'actual') == hashes(tmp_path/'reference')


@pytest.mark.parametrize('direction', [0., 90., 180., 240., 300., 359.999, -60.])
def test_world_geometry_and_meteorological_flow_rotate_together(direction):
    world = np.array([[100., 200., 0.], [604., 200., 0.], [1108., 200., 0.]])
    solver, transform = rotate_layout(world, direction)
    matrix = np.array(transform['world_to_solver_matrix'])
    offset = np.array(transform['translation_m'])
    np.testing.assert_allclose((solver[:, :2]-offset) @ matrix, world[:, :2], atol=1e-10)
    heading = np.radians(270.-direction)
    np.testing.assert_allclose(matrix @ [np.cos(heading), np.sin(heading)], [1., 0.], atol=1e-10)
    assert np.ptp(solver[:, 1]) == pytest.approx(abs(1008*np.sin(heading)))


@pytest.mark.parametrize('direction', [True, np.bool_(False), '300', np.nan, np.inf, None])
def test_invalid_explicit_direction_fails_before_writing(tmp_path, direction):
    with pytest.raises(ValueError, match='finite meteorological'):
        simul_utils.create_ff_case(case_args(direction=direction), str(tmp_path/'invalid'))
    assert not (tmp_path/'invalid').exists()


@pytest.mark.parametrize('direction', [0., 90., 180., 300.])
def test_steady_domains_contain_rotated_turbines(tmp_path, direction):
    farm = simul_utils.create_ff_case(case_args(direction=direction), str(tmp_path))
    identity = json.loads(Path(farm).with_suffix('.direction.json').read_text(encoding='utf-8'))
    for bounds in identity['transform']['domain_coverage'].values():
        low, high = bounds['low_bounds_m']
        assert all(low-1e-3 <= start < end <= high+1e-3 for start, end in bounds['high_bounds_m'])
    assert identity['transform']['direction_deg'] == direction
    inputs = simul_utils.FASTInputFile(farm)
    assert inputs['DT_mooring'] <= inputs['DT_High']
    assert inputs['DT_High'] == identity['reference_geometry']['DT_High']
    assert simul_utils.FASTInputFile(simul_utils.get_inflow_file_path(farm))['PropagationDir'] == 0


def test_bts_300_domain_coverage_and_narrow_box_rejection(tmp_path):
    args = case_args(direction=300.)
    args['wind_time_series'] = 'ts_u8_ti8_s42'
    farm = simul_utils.create_ff_case(args, str(tmp_path/'wide'))
    saved = json.loads(Path(farm).with_suffix('.direction.json').read_text(encoding='utf-8'))
    bounds = saved['transform']['domain_coverage']['Y']
    assert min(b[0] for b in bounds['high_bounds_m']) >= -340
    assert max(b[1] for b in bounds['high_bounds_m']) <= 340
    with pytest.raises(ValueError, match='exceed ambient domain'):
        prepare_direction(farm, 0.)
    args['wind_time_series'] = '90m_08mps.bts'
    args['xcoords'] = [0., 2000., 4000.]
    with pytest.raises(ValueError, match='exceed ambient domain'):
        simul_utils.create_ff_case(args, str(tmp_path/'narrow'))


@pytest.fixture
def mpi_case(tmp_path, monkeypatch):
    class Peer:
        def Get_rank(self): return 0
        def Send(self, **kwargs): pass
        def Barrier(self): pass
        def Disconnect(self): pass
        def Recv(self, buffer, **kwargs):
            if buffer.size == 1:
                buffer[:] = 12
            else:
                values = np.zeros((3, 12))
                values[:, 0], values[:, 1] = 8., 1e6
                values[:, 2] = np.radians([0., 2., -3.])
                buffer[:] = values.ravel()
    peer = Peer()
    launches = []
    def spawn(*args, **kwargs):
        launches.append((args, kwargs))
        return peer
    monkeypatch.setattr(interface, 'MPI', SimpleNamespace(COMM_WORLD=peer,
        COMM_SELF=SimpleNamespace(Spawn=spawn), Intercomm=Peer))
    farm = simul_utils.create_ff_case(case_args(), str(tmp_path/'case'))
    itf = interface.FastFarmInterface(3, farm, max_iter=3, fast_farm_executable=sys.executable)
    return itf, Path(farm), launches


def test_reset_direction_identity_yaw_semantics_and_no_accumulating_rotation(mpi_case):
    itf, farm, launches = mpi_case
    original = farm.read_bytes()
    for direction in (300., 240., 270.):
        itf.set_wind_request_context(dict(wind_direction=direction))
        record = itf.init(wind_direction=direction)
        assert record['requested']['wind_direction'] == direction
        assert record['application']['wind_direction']['value'] == direction
        expected, _ = rotate_layout([[0., 0., 0.], [504., 0., 0.], [1008., 0., 0.]], direction)
        np.testing.assert_allclose([t['position_m'] for t in record['loaded_inputs']['turbines']], expected, atol=.001)
        for _ in range(3):
            itf.update_command(yaw=np.array([0., 10., -10.]))
        np.testing.assert_allclose(itf.get_measure('wind_direction'), [direction, direction+2, direction-3])
        np.testing.assert_allclose(itf.get_yaw_command(), [0., 10., -10.])
    assert farm.read_bytes() == original
    assert len(launches) == 3


def test_mismatched_intent_and_mid_episode_changes_are_rejected(mpi_case):
    itf, farm, launches = mpi_case
    original = farm.read_bytes()
    itf.set_wind_request_context(dict(wind_direction=300.))
    with pytest.raises(interface.WindRequestError, match='does not match'):
        itf.init(wind_direction=240.)
    assert farm.read_bytes() == original and not launches
    itf.init(wind_direction=300.)
    with pytest.raises(RuntimeError, match='during an episode'):
        itf.init(wind_direction=270.)
    with pytest.raises(NotImplementedError, match='mid-run'):
        itf.update_wind(wind_direction=270.)
    assert len(launches) == 1


def test_rotated_file_reload_preserves_default_direction(tmp_path):
    farm = simul_utils.create_ff_case(case_args(direction=300.), str(tmp_path))
    itf = interface.FastFarmInterface(3, farm)
    itf._prepare_wind_record(None, None, {})
    assert itf.wind_record['application']['wind_direction']['value'] == 300.
    assert itf.wind_record['coordinates']['transform']['world_positions_m'][2] == [1008., 0., 0.]


def test_scene_and_training_entrypoints_preserve_direction(tmp_path):
    from scripts.train.train_fastfarm import experiment_config, training_wind_schedule
    from wfrl.windcond import resolve, make_wind_schedule
    scene = load_scene(Path(__file__).resolve().parents[1]/'scenes/turb3_row.yaml')
    scene.wind_direction = 300.
    assert scene.make_case(3).dict()['direction'] == 300.
    assert resolve('veer')[:2] == (8., 240.)
    assert make_wind_schedule('preset:veer')(0) == (8., 240.)
    config = experiment_config('Dec_Turb3_Row1_Fastfarm', 1, 1, wind_speed=8., wind_direction=300.)
    assert training_wind_schedule(config)(0) == (8., 300.)


def test_failed_readback_rolls_back_farm_inflow_and_direction_identity(mpi_case, monkeypatch):
    itf, farm, launches = mpi_case
    inflow = Path(itf._inflow_file)
    original = (farm.read_bytes(), inflow.read_bytes())
    inspect_inputs = itf._inspect_wind_inputs
    calls = 0
    def changed(path=None):
        nonlocal calls
        calls += 1
        result = inspect_inputs(path)
        if calls == 2:
            result['turbines'][0]['position_m'][0] += 100
        return result
    monkeypatch.setattr(itf, '_inspect_wind_inputs', changed)
    with pytest.raises(interface.WindRequestError, match='readback'):
        itf.init(wind_speed=10., wind_direction=300.)
    assert (farm.read_bytes(), inflow.read_bytes()) == original
    assert not farm.with_suffix('.direction.json').exists()
    assert not launches and itf._active_direction_deg == 270.


def test_lost_direction_request_is_not_silently_applied(mpi_case):
    itf, farm, launches = mpi_case
    itf.set_wind_request_context(dict(wind_direction=300.))
    with pytest.raises(interface.WindRequestError, match='not forwarded'):
        itf.init()
    assert not launches


def test_native_summary_refuses_output_until_solver_process_exit(tmp_path, monkeypatch):
    from scripts.experiments import compare_fastfarm_directions as native
    monkeypatch.setattr(native, 'solver_processes', lambda farm: [object()])
    with pytest.raises(RuntimeError, match='while FAST.Farm is running'):
        native.summarize(tmp_path/'Case.fstf', 180)


def test_aligned_direction_conversion_keeps_exact_floating_point_values(mpi_case):
    itf, _, _ = mpi_case
    itf.init()
    raw = np.array([1e-14, .123456789012345, -12.3456789012345])
    def receive(buffer, **kwargs):
        values = np.zeros((3, 12))
        values[:, 0], values[:, 2] = 8., raw
        buffer[:] = values.ravel()
    itf._comm.Recv = receive
    itf.update_command()
    np.testing.assert_array_equal(itf.get_measure('wind_direction'), (np.degrees(raw)-90.) % 360.)
