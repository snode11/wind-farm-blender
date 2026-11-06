"""Driver fallback diagnostics and cleanup ownership without simulator processes."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from wfrl import fastfarm_driver as module
from wfrl.viz import wakevtk


def driver_with_interface(interface):
    driver = module.FastFarmDriver.__new__(module.FastFarmDriver)
    driver.env = NS(mdp=NS(interface=interface))
    driver.n = 2
    return driver


def assert_logged(caplog, error, context):
    records = [record for record in caplog.records if record.name == module.__name__]
    assert any(context in record.getMessage() and record.exc_info
               and record.exc_info[1] is error for record in records)
    assert type(error).__name__ in caplog.text
    assert str(error) in caplog.text


def constructor_env(interface):
    return NS(
        mdp=NS(interface=interface, farm_case=NS(xcoords=[0.], ycoords=[0.], dt=3.)),
        possible_agents=["T1"],
        action_space=lambda agent: {"yaw": NS(low=np.array([-5.]), high=np.array([5.]))},
        observation_space=lambda agent: {"yaw": object()},
    )


def test_missing_case_path_is_an_optional_capability(monkeypatch, caplog):
    monkeypatch.setattr(module.envs, "make", lambda *a, **kw: constructor_env(NS()))
    monkeypatch.setattr(wakevtk, "case_dir_of", Mock(side_effect=AssertionError("no case file")))
    driver = module.FastFarmDriver("fixture_floris", yaw_duty_sliding=False)
    assert driver.case_dir is None
    assert not caplog.records


def test_invalid_case_path_logs_the_type_and_keeps_cleanup_path_unset(monkeypatch, caplog):
    monkeypatch.setattr(module.envs, "make", lambda *a, **kw: constructor_env(NS(_simul_file=object())))
    driver = module.FastFarmDriver("fixture_floris", yaw_duty_sliding=False)
    assert driver.case_dir is None
    assert "TypeError" in caplog.text and "算例目录" in caplog.text


def test_case_directory_programming_error_propagates(monkeypatch):
    monkeypatch.setattr(module.envs, "make", lambda *a, **kw: constructor_env(NS(_simul_file="case.fstf")))
    error = RuntimeError("case resolver is broken")
    monkeypatch.setattr(wakevtk, "case_dir_of", Mock(side_effect=error))
    with pytest.raises(RuntimeError) as raised:
        module.FastFarmDriver("fixture_floris", yaw_duty_sliding=False)
    assert raised.value is error


@pytest.mark.parametrize("value,exception", [
    ([], IndexError), (["invalid"], ValueError), ([object()], TypeError),
    ([10 ** 400], OverflowError),
])
def test_malformed_floris_wind_logs_before_using_fallback(value, exception, caplog):
    driver = driver_with_interface(NS(fi=NS(floris=NS(flow_field=NS(wind_speeds=value)))))
    assert driver._freestream(fallback=8.) == 8.
    assert exception.__name__ in caplog.text and "FLORIS" in caplog.text


def test_missing_floris_wind_attribute_logs_before_fallback(caplog):
    driver = driver_with_interface(NS(fi=NS()))
    assert np.isnan(driver._freestream())
    assert "AttributeError" in caplog.text


def test_valid_floris_wind_is_not_replaced_by_fallback(caplog):
    driver = driver_with_interface(NS(fi=NS(floris=NS(flow_field=NS(wind_speeds=[9.])))))
    assert driver._freestream(fallback=8.) == 9.
    assert not caplog.records


def test_unexpected_floris_failure_is_not_a_missing_wind_reading():
    class FlowField:
        @property
        def wind_speeds(self):
            raise RuntimeError("flow field is broken")

    driver = driver_with_interface(NS(fi=NS(floris=NS(flow_field=FlowField()))))
    with pytest.raises(RuntimeError, match="flow field is broken"):
        driver._freestream(fallback=8.)


@pytest.mark.parametrize("fault", ["missing", "empty"])
def test_inflow_file_failures_log_real_parser_errors(tmp_path, fault, caplog):
    path = tmp_path / "InflowWind.dat"
    if fault == "empty":
        path.touch()
    driver = driver_with_interface(NS(_inflow_file=str(path)))
    assert driver._freestream(fallback=8.) == 8.
    assert str(path) in caplog.text
    assert ("EmptyFileError" if fault == "empty" else "FileNotFoundError") in caplog.text


@pytest.mark.parametrize("error_type", [KeyError, TypeError, ValueError, OverflowError])
def test_inflow_conversion_failures_are_diagnosed(tmp_path, monkeypatch, caplog, error_type):
    from wfcrl import simul_utils
    path = tmp_path / "fixture.dat"
    path.write_text("nonempty input", encoding="utf-8")
    error = error_type("inflow value unavailable")
    monkeypatch.setattr(simul_utils, "read_inflow_info", Mock(side_effect=error))
    driver = driver_with_interface(NS(_inflow_file=str(path)))
    assert np.isnan(driver._freestream())
    assert_logged(caplog, error, "入流文件")


def test_unexpected_inflow_reader_failure_propagates(tmp_path, monkeypatch):
    from wfcrl import simul_utils
    path = tmp_path / "fixture.dat"
    path.write_text("nonempty input", encoding="utf-8")
    monkeypatch.setattr(simul_utils, "read_inflow_info", Mock(side_effect=RuntimeError("reader bug")))
    driver = driver_with_interface(NS(_inflow_file=str(path)))
    with pytest.raises(RuntimeError, match="reader bug"):
        driver._freestream(fallback=8.)


@pytest.mark.parametrize("error_type", [KeyError, IndexError, TypeError, ValueError, OverflowError])
def test_unavailable_raw_measure_is_logged_and_remains_missing(caplog, error_type):
    error = error_type("measurement unavailable")
    driver = driver_with_interface(NS(measure_map={"torque": 5}, get_measure=Mock(side_effect=error)))
    assert driver._raw_measure("torque") is None
    assert_logged(caplog, error, "torque")


def test_raw_measure_conversion_failure_logs_channel(caplog):
    driver = driver_with_interface(NS(measure_map={"torque": 5}, get_measure=lambda name: ["bad", 2.]))
    assert driver._raw_measure("torque") is None
    assert "ValueError" in caplog.text and "torque" in caplog.text


def test_unsupported_measure_does_not_call_interface_or_log(caplog):
    get_measure = Mock(side_effect=AssertionError("unsupported channel"))
    driver = driver_with_interface(NS(measure_map={}, get_measure=get_measure))
    assert driver._raw_measure("torque") is None
    get_measure.assert_not_called()
    assert not caplog.records


def test_unexpected_measurement_error_propagates():
    driver = driver_with_interface(NS(measure_map={"torque": 5},
                                      get_measure=Mock(side_effect=RuntimeError("measurement bug"))))
    with pytest.raises(RuntimeError, match="measurement bug"):
        driver._raw_measure("torque")


@pytest.mark.parametrize("strict", [False, True])
def test_drain_and_close_failures_are_both_logged_and_close_is_attempted(caplog, strict):
    drain_error = ValueError("drain failed")
    close_error = RuntimeError("close failed")
    driver = driver_with_interface(NS())
    driver._started, driver.done, driver._steps = True, False, 0
    driver.max_steps, driver.n, driver.controls = 2, 1, ["yaw"]
    driver.env.last = Mock(side_effect=drain_error)
    driver.env.close = Mock(side_effect=close_error)
    driver._purge_case = Mock()
    if strict:
        with pytest.raises(ValueError) as raised:
            driver.close(purge=True, strict=True)
        assert raised.value is drain_error
        driver._purge_case.assert_not_called()
        with pytest.raises(RuntimeError, match="previously failed"):
            driver.close(strict=True)
        driver.env.last.assert_called_once()
    else:
        driver.close(purge=True)
        driver._purge_case.assert_called_once()
    driver.env.close.assert_called_once()
    assert_logged(caplog, drain_error, "排空")
    assert_logged(caplog, close_error, "关闭")


def test_non_strict_safe_close_logs_backend_failure(caplog):
    error = RuntimeError("backend cleanup failed")
    driver = driver_with_interface(NS())
    driver.env.close = Mock(side_effect=error)
    driver._safe_close()
    assert_logged(caplog, error, "关闭")


@pytest.mark.parametrize("error_type", [PermissionError, RuntimeError])
def test_case_purge_retries_only_filesystem_errors(tmp_path, monkeypatch, caplog, error_type):
    import shutil
    import time
    path = tmp_path / "__simul__" / "fixture"
    path.mkdir(parents=True)
    driver = driver_with_interface(NS())
    driver.case_dir = str(path)
    error = error_type("cannot delete case")
    remove = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(module, "_PENDING_PURGE", [])
    monkeypatch.setattr(shutil, "rmtree", remove)
    monkeypatch.setattr(time, "sleep", sleep)
    if error_type is PermissionError:
        driver._purge_case()
        assert remove.call_count == 3 and sleep.call_count == 2
        assert module._PENDING_PURGE == [str(path)]
        assert_logged(caplog, error, str(path))
    else:
        with pytest.raises(RuntimeError) as raised:
            driver._purge_case()
        assert raised.value is error
        remove.assert_called_once()
        sleep.assert_not_called()
    assert path.is_dir()
