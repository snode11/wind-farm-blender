"""Solver completion and final-output checks are independent of MPI disconnect."""
import threading

import pytest

from scripts.experiments.compare_fastfarm_physics import summarize, wait_for_normal_termination


def native(path, times):
    path.write_text('Time GenPwr RootMyc1 RootMxc1 TwrBsMxt TwrBsMyt TTDspFA TTDspSS\n'
                    '(s) (kW) (kN-m) (kN-m) (kN-m) (kN-m) (m) (m)\n' +
                    ''.join(f'{time} 1000 10 2 4 5 .1 -.02\n' for time in times), encoding='utf-8')


def test_mpi_disconnect_is_not_native_completion(tmp_path):
    log = tmp_path / 'solver.log'
    log.write_text('Finalizing MPI Communication...\n', encoding='utf-8')
    with pytest.raises(TimeoutError, match='normal termination not confirmed'):
        wait_for_normal_termination(log, .03)


def test_wait_includes_delayed_final_output_and_native_termination(tmp_path):
    log, output = tmp_path / 'solver.log', tmp_path / 'Case.T1.out'
    log.write_text('Finalizing MPI Communication...\n', encoding='utf-8')
    native(output, [44.5, 44.75])

    def complete():
        with output.open('a', encoding='utf-8') as stream:
            stream.write('45 1000 10 2 4 5 .1 -.02\n')
        with log.open('a', encoding='utf-8') as stream:
            stream.write('Time: 45 of 45 seconds.\nFAST.Farm terminated normally.\n')

    writer = threading.Timer(.05, complete)
    writer.start()
    try:
        wait_for_normal_termination(log, 1.)
        assert summarize(output, 45)['samples'] == 3
    finally:
        writer.join()


@pytest.mark.parametrize('last', [44.5, 44.75, 45.25])
def test_normal_marker_does_not_relax_final_timestamp(tmp_path, last):
    log, output = tmp_path / 'solver.log', tmp_path / 'Case.T1.out'
    log.write_text('FAST.Farm terminated normally.\n', encoding='utf-8')
    native(output, [22.5, last])
    wait_for_normal_termination(log, 1.)
    with pytest.raises(RuntimeError, match='Incomplete native output'):
        summarize(output, 45)


def test_unterminated_last_row_is_incomplete(tmp_path):
    output = tmp_path / 'Case.T1.out'
    native(output, [22.5, 45.])
    output.write_bytes(output.read_bytes().rstrip(b'\n'))
    with pytest.raises(RuntimeError, match='Incomplete native output'):
        summarize(output, 45)
