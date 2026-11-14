"""Short smoke runs preserve defaults and wait for native output completion."""
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

from scripts.inflow.smoke_turbulence import native_summary, run_worker, short_scene, wait_solver_exit


def test_short_scene_changes_only_the_explicit_smoke_warmup():
    from wfrl.scene import load_scene

    root = Path(__file__).resolve().parents[1]
    source = load_scene(root / "scenes" / "turb3_row.yaml")
    source.turbulence = "ts_u12_ti16_s42"
    smoke = short_scene(source)
    assert smoke.to_dict() == source.to_dict()
    assert smoke.make_case(20).t_init == 0
    assert smoke.make_case(20).wind_time_series == "ts_u12_ti16_s42"
    assert source.make_case(20).t_init == 100


def test_native_marker_alone_does_not_allow_reading_before_process_exit(tmp_path):
    log, output = tmp_path / "solver.log", tmp_path / "native.out"
    log.write_text("FAST.Farm terminated normally.\n", encoding="utf-8")
    code = "import pathlib,time,sys; time.sleep(.2); pathlib.Path(sys.argv[1]).write_bytes(b'final row\\n')"
    with subprocess.Popen([sys.executable, "-c", code, str(output)]) as process:
        native = psutil.Process(process.pid)
        wait_solver_exit(native, log, timeout=5)
        assert output.read_bytes() == b"final row\n"
        assert process.poll() is not None


def test_native_exit_without_normal_marker_is_rejected(tmp_path):
    log = tmp_path / "solver.log"
    log.write_text("Finalizing MPI Communication...\n", encoding="utf-8")
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.1)"]) as process:
        with pytest.raises(RuntimeError, match="没有正常结束"):
            wait_solver_exit(psutil.Process(process.pid), log, timeout=5)


def test_smoke_timeout_leaves_unrelated_sibling_process_running(tmp_path):
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as other:
        try:
            assert run_worker([sys.executable, "-c", "import time; time.sleep(30)"],
                              tmp_path, dict(os.environ), timeout=.1) == 124
            assert other.poll() is None
        finally:
            other.terminate()
            other.wait(timeout=5)


@pytest.mark.parametrize("end,terminated", [(29.75, True), (30., False)])
def test_final_native_statistics_reject_truncated_output(tmp_path, end, terminated):
    path = tmp_path / "Case.T1.out"
    text = f"Time GenPwr RotSpeed\n(s) (kW) (rpm)\n0 0 12\n{end} 1000 12"
    path.write_text(text + ("\n" if terminated else ""), encoding="utf-8")
    with pytest.raises(RuntimeError, match="不完整|末时刻"):
        native_summary(path, 30.)
