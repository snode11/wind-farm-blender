"""Training snapshots retain their episode identity in plotted history."""
from types import SimpleNamespace as NS

import pytest

pytest.importorskip('numpy')

from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl_blender.charts import ChartHistory, segments


def adapter():
    scene = NS(turbines=[NS(id='T1')], dt=3., controls=['yaw'], backend='fastfarm')
    return SnapshotAdapter(scene, 'session', mode='interactive_training')


def source(episode, time_s, power):
    return NS(step=4, episode=episode, ctx={'t': time_s}, measure={'power': [power]},
              farm_power=power, reward=0.)


def test_adapter_preserves_two_episode_boundaries_at_the_same_global_step():
    producer = adapter()
    history = ChartHistory()
    first = producer.encode(source(0, 12., 2.))
    second = producer.encode(source(1, 0., 3.))
    assert first['episode'] == 0
    assert second['episode'] == 1

    history.ingest(dict(session_id='session', sequence=1, payload=first))
    history.ingest(dict(session_id='session', sequence=2, payload=second))

    points = history.points('T1', 'power')
    assert [(point.step, point.episode, point.time, point.value) for point in points] == [
        (4, 0, 12., 2.), (4, 1, 0., 3.)]
    assert segments(points) == [[(12., 2.)], [(0., 3.)]]

    second['turbines'][0]['channels']['power']['value'] = 3.5
    history.ingest(dict(session_id='session', sequence=3, payload=second))
    assert [point.value for point in history.points('T1', 'power')] == [2., 3.5]


@pytest.mark.parametrize('episode', [0, 2])
def test_wire_episode_takes_precedence_over_legacy_nested_context(episode):
    payload = adapter().encode(source(episode, 0., 3.))
    payload['context'] = {'episode': 99}
    history = ChartHistory()
    history.ingest(dict(session_id='session', sequence=1, payload=payload))
    assert history.points('T1', 'power')[0].episode == episode
