"""Chart gaps, finite drawing geometry and private stream diagnostics."""
from collections import deque
import io
import math
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import charts, live_stream
from wfrl_blender.history import HistoryWindow
from wfrl_blender.protocol import TRAINING_STATS_CHANNELS, validate_message


@pytest.fixture
def chart_history(monkeypatch):
    charts.invalidate_render_cache()
    monkeypatch.setattr(charts, 'history', charts.ChartHistory())
    monkeypatch.setattr(charts, 'raw_history', HistoryWindow())
    yield charts.history
    charts.invalidate_render_cache()


def _curve(sequence, values, *, validity='valid', episode=0):
    return dict(protocol_version=1, type='curve', session_id='ci-chart', sequence=sequence,
                payload=dict(mode='replay', run_id='ci-run', step=sequence, episode=episode,
                             timestamp=dict(value=float(sequence), timebase='simulation_seconds'),
                             turbine_id='T1', channel='power', data=dict(
                                 value=values, validity=validity, unit='MW', fidelity='DIRECT',
                                 error=None if validity == 'valid' else 'No observation',
                                 provenance=dict(backend_session='ci-chart', channel='power'),
                                 source_age_seconds=0., stale_after_seconds=2.)))


@pytest.mark.parametrize('validity', ['invalid', 'waiting', 'unsupported', 'stale'])
def test_null_curve_record_breaks_the_line_without_rewriting_raw_data(chart_history, validity):
    messages = [_curve(1, [[0., 1.]]), _curve(2, None, validity=validity), _curve(3, [[3., 4.]])]
    for message in messages:
        validate_message(message)
        charts.record_curve(message)

    assert charts.segments(chart_history.points('T1', 'power')) == [[(0., 1.)], [(3., 4.)]]
    raw = charts.raw_history.snapshot()['series'][0]['records']
    assert [record['data'] for record in raw] == [message['payload']['data'] for message in messages]


def test_curve_episode_boundary_is_retained_even_when_time_increases(chart_history):
    for sequence, episode in ((1, 0), (2, 1)):
        message = _curve(sequence, [[float(sequence), float(sequence)]], episode=episode)
        validate_message(message)
        charts.record_curve(message)
    points = chart_history.points('T1', 'power')
    assert charts.segments(points) == [[(1., 1.)], [(2., 2.)]]
    assert [point.episode for point in points] == [0, 1]


@pytest.mark.parametrize(('source_age', 'validity'), [(29.99, 'valid'), (30., 'stale'), (31., 'stale')])
def test_training_curve_respects_each_statistic_source_expiry(chart_history, source_age, validity):
    stats = {name: dict(value=2., validity='valid', unit='MW' if name == 'mean_power' else '',
                       fidelity='DIRECT', error=None,
                       provenance=dict(backend_session='ci-chart', channel=name),
                       source_age_seconds=source_age if name == 'mean_power' else 0.,
                       stale_after_seconds=30.) for name in TRAINING_STATS_CHANNELS}
    message = dict(protocol_version=1, type='training_stats', session_id='ci-chart', sequence=1,
                   payload=dict(mode='formal_training', run_id='ci-run', step=1, iteration=1, agent_step=1,
                                phase='updating', record_kind='iteration_stats', stats=stats,
                                timestamp=dict(value=1.e9, timebase='unix_seconds'),
                                source_age_seconds=0., stale_after_seconds=30.))
    validate_message(message)
    charts.record_training(message)

    point = chart_history.points(charts.FARM, 'mean_power')[0]
    assert point.validity == validity
    assert point.value == (2. if validity == 'valid' else None)
    assert chart_history.points(charts.FARM, 'mean_reward')[0].value == 2.
    raw = charts.raw_history.series[('training_stats', 'iteration_stats', None, 'mean_power')][0]
    assert raw['data'] == stats['mean_power']


@pytest.mark.parametrize(('time', 'value', 'validity'), [
    (1., None, 'valid'), (1., math.nan, 'valid'), (1., math.inf, 'valid'),
    (1., -math.inf, 'valid'), (1., 100., 'invalid'), (1., True, 'valid'),
    (math.nan, 100., 'valid'), (math.inf, 100., 'valid'), (None, 100., 'valid'),
])
def test_invalid_plot_points_are_gaps_and_do_not_change_bounds(chart_history, time, value, validity):
    def point(stamp, sample, state='valid'):
        return charts.Point(0, stamp, sample, 'MW', 'DIRECT', state, {})

    chart_history.series[('T1', 'power')] = deque([
        point(0., 0.), point(time, value, validity), point(2., 2.)])
    data = charts._plot_data('power', 'T1')
    assert data.bounds == (0., 2., 0., 2.)
    assert data.series[0][1] == (((0., 0.),), ((2., 2.),))


@pytest.mark.parametrize('values', [[], [math.nan, math.inf, -math.inf, None]])
def test_axes_without_finite_samples_have_no_ticks(values):
    assert charts.axis_ticks(values) == ()


@pytest.mark.parametrize('values', [
    [0., 1.e308], [-1.e308, 1.e308], [sys.float_info.max], [-sys.float_info.max],
])
def test_extreme_finite_samples_have_finite_ticks_and_plot_coordinates(chart_history, values):
    message = _curve(1, [[value, value] for value in values])
    validate_message(message)
    charts.record_curve(message)
    data = charts._plot_data('power', 'T1')
    ticks = charts.axis_ticks(values)
    assert all(math.isfinite(value) for value in ticks)
    assert all(a < b for a, b in zip(ticks, ticks[1:]))
    assert ticks[0] <= min(values) <= max(values) <= ticks[-1]
    blf = NS(size=lambda *_args: None, dimensions=lambda _font, text: (len(text) * 5, 10))
    commands = charts._plot_commands(data, 1., 660., blf)
    for command in commands:
        coords = command[1] if command[0] == 'line' else [command[1:3]]
        assert all(math.isfinite(value) for coordinate in coords for value in coordinate)
    assert charts.raw_history.snapshot()['series'][0]['records'][0]['data'] == message['payload']['data']


@pytest.mark.parametrize('values', [[0.], [-2.], [1.7e9], [-12., 5.], [9.025, 9.04]])
def test_ordinary_axis_ticks_keep_their_exact_values(values):
    low, high = min(values), max(values)
    if low == high:
        pad = max(abs(low) * 1.e-6, .5)
        low, high = low - pad, high + pad
    assert charts.axis_ticks(values) == tuple(low + (high - low) * i / 4 for i in range(5))


_PRIVATE_URL = 'rtsp://ci_user:ci_password@camera.local/live?token=ci_token'


def _assert_private(text):
    assert all(secret not in text for secret in ('ci_user', 'ci_password', 'ci_token'))


def test_malformed_url_validation_does_not_echo_credentials():
    url = _PRIVATE_URL.replace('camera.local', 'camera\uff1a80')
    with pytest.raises(ValueError) as error:
        live_stream.check_url(url)
    _assert_private(str(error.value))


@pytest.mark.parametrize('failure', ['read', 'reader_start'])
def test_pipe_failure_status_is_private_and_owned_pipes_close(monkeypatch, failure):
    process = NS(stdout=io.BytesIO(), stderr=io.BytesIO(), returncode=1,
                 terminate=lambda: None, kill=lambda: None, wait=lambda **_kwargs: 1)
    monkeypatch.setattr(live_stream, '_spawn', lambda *_args: process)
    monkeypatch.setattr(live_stream, '_process_running', lambda _process: False)
    if failure == 'read':
        def fail_read(*_args):
            raise OSError(f'Cannot read {_PRIVATE_URL}')
        monkeypatch.setattr(live_stream, '_read_exact', fail_read)
    else:
        original_start = live_stream.threading.Thread.start

        def fail_start(thread):
            if thread.name == 'wfrl-live-stderr':
                raise RuntimeError(f'Cannot start reader for {_PRIVATE_URL}')
            return original_start(thread)
        monkeypatch.setattr(live_stream.threading.Thread, 'start', fail_start)

    stream = live_stream.LiveStream('ffmpeg', _PRIVATE_URL, (1, 1))
    try:
        stream.thread.join(timeout=5)
        assert not stream.active and stream.state == 'ERROR'
        assert process.stdout.closed and process.stderr.closed
        assert 'Cannot' in stream.message
        _assert_private(stream.message)
    finally:
        stream.stop()
        stream.thread.join(timeout=5)
