"""Instrumentation must remain opt-in and preserve wrapped behavior."""
import pytest
from wfrl_blender import performance as p


@pytest.mark.parametrize('raw,platform', [(102400, 'linux'), (104857600, 'darwin')])
def test_peak_rss_units_report_the_same_memory(raw, platform):
    assert p.peak_rss_mib(raw, platform=platform) == 100.


def test_disabled_and_exception_path():
    @p.timed('failure')
    def fail():
        raise ValueError('original')
    p.configure(False)
    with pytest.raises(ValueError, match='original'):
        fail()
    p.count('ignored')
    assert p.snapshot()['timings'] == {}
    p.configure(True)
    try:
        with pytest.raises(ValueError, match='original'):
            fail()
        assert p.snapshot()['timings']['failure']['calls'] == 1
        p.count('vertices', 120)
        assert p.snapshot()['counts']['vertices'] == 120
        p.reset()
        assert p.snapshot()['timings'] == {}
        assert p.snapshot()['counts'] == {}
    finally:
        p.configure(False)
