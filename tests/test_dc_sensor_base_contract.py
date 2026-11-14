"""Sensor scheduling failures remain isolated to their own output topics."""
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.channels.base import Sensor


class ProbeSensor(Sensor):
    type_name = 'probe'
    rate_hz = .2

    @property
    def topics(self):
        return ['first', 'second']

    def sample(self, ctx):
        self.samples += 1
        return {'first': ctx['value'], 'second': ctx['value']}


@pytest.fixture
def sensor():
    scene = SimpleNamespace(n=1, dt=3., backend='fastfarm')
    sensor = ProbeSensor(scene, SimpleNamespace(params=None), [0])
    sensor.samples = 0
    return sensor


@pytest.mark.parametrize('field,value', [
    (field, value) for field in ('rate_hz', 'dt') for value in (0., -1., np.nan, np.inf, True)
] + [('dt', None)])
def test_step_reports_invalid_schedule_per_topic_without_consuming_sample(sensor, field, value):
    target = sensor if field == 'rate_hz' else sensor.scene
    original = getattr(target, field)
    setattr(target, field, value)
    result = sensor.step({'value': 7.})
    assert set(result) == {'first', 'second'}
    assert all('SceneErrorProxy' in reading['error'] for reading in result.values())
    assert sensor._last is result and sensor._k == 0 and sensor.samples == 0
    setattr(target, field, original)
    assert sensor.step({'value': 7.}) == {'first': 7., 'second': 7.}


def test_decimate_avoids_intermediate_reciprocal_overflow(sensor):
    sensor.rate_hz, sensor.scene.dt = 1e-309, 1e308
    result = [sensor.decimate() for _ in range(12)]
    assert result == [True] + [False] * 9 + [True, False]
    assert sensor.period_steps == 10


def test_step_keeps_regular_cadence_and_observation_values(sensor):
    outputs = [sensor.step({'value': value}) for value in [0., np.nan, None, 4., np.nan]]
    assert outputs[:4] == [{'first': 0., 'second': 0.}, None,
                           {'first': None, 'second': None}, None]
    assert all(np.isnan(value) for value in outputs[4].values())
    assert sensor.samples == 3 and sensor._k == 5
