"""Draw evidence stays bounded and does not report redraw frequency as FPS."""
from wfrl_blender import frontend_diagnostics as diagnostics


def test_duplicates_pause_stalls_and_short_samples_do_not_inflate_fps():
    diagnostics.clear()
    for index in range(61):
        now = index/30
        diagnostics.observe('A', index, now=now)
        diagnostics.observe('A', index, now=now+.001)
    assert abs(diagnostics.rate('A', now=2)-30) < 1e-8
    assert diagnostics.rate('A', now=2.6) is None
    diagnostics.observe('A', 61, now=2.7, playing=False)
    assert diagnostics.rate('A', now=2.7) is None
    diagnostics.observe('A', 62, now=2.8, enabled=False)
    assert diagnostics.rate('A', now=2.8) is None


def test_each_viewport_has_its_own_rate_and_route_storage_is_bounded():
    diagnostics.clear()
    for index in range(61):
        diagnostics.observe('A', index, now=index/30)
    for index in range(21):
        diagnostics.observe('B', index, now=index/10)
    assert abs(diagnostics.rate('A', now=2)-30) < 1e-8
    assert abs(diagnostics.rate('B', now=2)-10) < 1e-8
    assert diagnostics.rate('B', now=5) is None
    assert 'B' not in diagnostics._DRAWS
    for route in range(100):
        diagnostics.observe(route, 1, now=3+route/100)
    assert len(diagnostics._DRAWS) <= 32
