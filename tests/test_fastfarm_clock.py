from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfrl.fastfarm_clock import coupling_timestamp, servo_time_steps
from wfrl.fastfarm_driver import FastFarmDriver
from wfcrl.interface import FastFarmInterface


@pytest.mark.parametrize('index,interval,value', [(37, 3., 111.), (52, 2., 104.), (0, .5, 0.)])
def test_clock_uses_completed_exchanges_including_initialization(index, interval, value):
    stamp = coupling_timestamp(index, interval)
    assert stamp == dict(value=value, timebase='simulation_seconds', source='mpi_exchange_index',
                         exchange_index=index, interval_seconds=interval)


@pytest.mark.parametrize('index,interval', [(-1, 3), (True, 3), (1.5, 3), (2, 0), (2, -3),
                                           (2, True), (2, float('nan')), (2, float('inf'))])
def test_clock_rejects_invalid_indices_and_intervals(index, interval):
    with pytest.raises(ValueError):
        coupling_timestamp(index, interval)


def test_driver_keeps_policy_yaw_and_reads_measured_yaw_and_solver_clock(tmp_path):
    primary = tmp_path / 'turbine.fst'
    primary.write_text('OpenFAST input\nclock fixture\n0.00625 DT - integration time step\n', encoding='utf-8')
    interface = FastFarmInterface.__new__(FastFarmInterface)
    interface._num_iter, interface._default_avg_window = 37, 1
    interface.measure_map = {'yaw': 0, 'pitch': 1, 'torque': 2}
    interface.current_measures = np.array([[np.radians(1.25), np.radians(2.), 20000.]])
    interface.wind_record = {'loaded_inputs': {'farm_settings': {'DT_Low': 3},
        'turbines': [{'primary_file': {'path': str(primary)}}]}}
    assert servo_time_steps(interface.wind_record['loaded_inputs']) == [.00625]
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.n, driver.agents, driver._idx = 1, ['T1'], {'T1': 0}
    driver.controls, driver.done, driver._steps = ['yaw'], False, 2
    driver.yaw_duty_sliding = False
    driver.env = NS(mdp=NS(interface=interface, ACTUATORS_RATE={}),
                    observe=lambda _: {'yaw': [15.], 'pitch': [np.radians(2.)],
                                       'wind_speed': [8.], 'wind_direction': [270.]},
                    infos={'T1': {'power': 1.6, 'load': np.arange(6.) / 1e7}})
    driver._duty = lambda: {'T1': {}}

    m = driver._read(0.)

    np.testing.assert_allclose(m['yaw'], [15.])
    np.testing.assert_allclose(m['yaw_command'], [15.])
    np.testing.assert_allclose(m['yaw_measured'], [1.25])
    assert m['solver_clock']['value'] == 111.
    assert m['solver_clock']['servo_time_steps'] == [.00625]
    interface._num_iter = 1
    assert driver._read(0.)['solver_clock']['value'] == 3.


@pytest.mark.parametrize('count', [1, 3])
def test_raw_blade_moments_keep_turbine_and_blade_axes(count):
    values = np.arange(count * 6., dtype=float).reshape(count, 6) * 1e6
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.n = count
    driver.env = NS(mdp=NS(interface=NS(measure_map={'load': list(range(6))},
                                        get_measure=lambda name: values.squeeze())))
    actual = driver._raw_blade_moments()
    np.testing.assert_array_equal(actual, values)
    values[:] = 0.
    assert actual[0, 1] == 1e6
