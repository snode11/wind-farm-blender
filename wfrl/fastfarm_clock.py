"""Solver time reconstructed from the supported SC_DLL exchange contract."""
import math
from numbers import Integral, Real


def coupling_timestamp(exchange_index, interval_seconds):
    """SC_DLL first returns measurements at DT_Low, then once per exchange."""
    if (isinstance(exchange_index, bool) or not isinstance(exchange_index, Integral)
            or exchange_index < 0):
        raise ValueError('MPI exchange index must be a nonnegative integer')
    if (isinstance(interval_seconds, bool) or not isinstance(interval_seconds, Real)
            or not math.isfinite(interval_seconds) or interval_seconds <= 0):
        raise ValueError('MPI interval must be positive and finite')
    value = int(exchange_index) * float(interval_seconds)
    if not math.isfinite(value):
        raise ValueError('Solver timestamp must be finite')
    return dict(value=value, timebase='simulation_seconds', source='mpi_exchange_index',
                exchange_index=int(exchange_index), interval_seconds=float(interval_seconds))


def servo_time_steps(loaded_inputs):
    """Read each turbine's integration step for ServoDyn's previous-step outputs."""
    from wfcrl.simul_utils import FASTInputFile
    result = []
    for turbine in loaded_inputs['turbines']:
        path = turbine['primary_file']['path']
        dt = float(FASTInputFile(path)['DT'])
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError('Invalid turbine integration step: ' + path)
        result.append(dt)
    return result
