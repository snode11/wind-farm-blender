"""WFCRL interface import and FAST.Farm executable discovery on every platform."""
import subprocess
import sys

from wfcrl.interface import WINDOWS_FASTFARM_DEFAULT, default_fastfarm_executable


def find(system, environ, existing, python, which=lambda name: None):
    return default_fastfarm_executable(system, environ, python, which, existing.__contains__)


def test_windows_prefers_configured_executable():
    configured = r'C:\tools\FAST.Farm_x64_OMP.exe'
    existing = {configured, WINDOWS_FASTFARM_DEFAULT}
    assert find('windows', {'WFCRL_FASTFARM_EXECUTABLE': configured}, existing, r'C:\env\python.exe') == configured


def test_windows_falls_back_to_release_location_then_path():
    assert find('windows', {}, {WINDOWS_FASTFARM_DEFAULT}, r'C:\env\python.exe') == WINDOWS_FASTFARM_DEFAULT
    on_path = r'C:\bin\FAST.Farm_x64_OMP.exe'
    which = lambda name: on_path if name == 'FAST.Farm_x64_OMP.exe' else None
    assert find('windows', {}, {on_path}, r'C:\env\python.exe', which) == on_path


def test_windows_missing_executable_reports_release_location():
    assert find('windows', {}, set(), r'C:\env\python.exe') == WINDOWS_FASTFARM_DEFAULT


def test_unix_never_returns_placeholder():
    assert find('linux', {}, set(), '/opt/env/bin/python') is None
    assert find('darwin', {}, {'/opt/env/bin/FAST.Farm'}, '/opt/env/bin/python') == '/opt/env/bin/FAST.Farm'


def test_interface_imports_before_environments():
    code = "from wfcrl.interface import FastFarmInterface, FlorisInterface"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
