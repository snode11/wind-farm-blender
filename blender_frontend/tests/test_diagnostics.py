from wfrl_blender.diagnostics import report, recovery_hint
from wfrl_blender.health import HealthChecker
from test_health import executable


def test_full_report_preserves_tail_unicode_and_source():
    detail = '错误路径\n' + 'x' * 5000 + '\nROOT CAUSE'
    result = report('Connection', detail, provenance={'file': '/路径/日志'})
    assert detail in result
    assert '/路径/日志' in result
    assert 'Suggested action' in result


def test_recovery_hints():
    assert '3.11+' in recovery_hint('python', 'UNSUPPORTED')
    assert 'port' in recovery_hint(detail='Connection refused')
    assert 'permissions' in recovery_hint(detail='Permission denied')
    assert recovery_hint('python', 'READY') == ''


def test_probe_preserves_error_after_old_limit(tmp_path):
    tool = executable(tmp_path, "import sys\nprint('x' * 4000 + 'ROOT CAUSE')\nsys.exit(1)\n")
    result = HealthChecker()._probe('mpi', tool)
    assert result.status == 'ERROR'
    assert result.detail.endswith('ROOT CAUSE')


def test_probe_marks_output_limit(tmp_path):
    tool = executable(tmp_path, "print('x' * 70000)\n")
    result = HealthChecker()._probe('mpi', tool)
    assert 'remaining output omitted' in result.detail
