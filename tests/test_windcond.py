"""Episode schedules validate their grammar and preserve reproducible sampling."""
import numpy as np
import pytest

from wfrl.windcond import WS_BOUNDS, make_wind_schedule, resolve


@pytest.mark.parametrize("spec", ["preset:missing", "unknown", "cycle:", "cycle:6,,8", 7])
def test_invalid_schedule_is_catchable_as_value_error(spec):
    with pytest.raises(ValueError):
        make_wind_schedule(spec)


def test_unknown_preset_or_backend_does_not_leak_key_or_type_error():
    for name in ("missing", []):
        with pytest.raises(ValueError):
            resolve(name)
    with pytest.raises(ValueError, match="后端"):
        make_wind_schedule("preset:veer", backend="florsi")


@pytest.mark.parametrize("seed", [-1, True, 1.9, np.nan, np.inf, None])
def test_weibull_seed_is_a_nonnegative_integer(seed):
    with pytest.raises(ValueError, match="seed"):
        make_wind_schedule("weibull", seed=seed)


@pytest.mark.parametrize("spec", ["pin:8", "preset:calm", "cycle:6,8,10", "weibull", "none"])
def test_every_schedule_rejects_invalid_episode_indices(spec):
    schedule = make_wind_schedule(spec)
    for episode in (-1, True, 1.5, np.nan, None, "2"):
        with pytest.raises(ValueError, match="episode_index"):
            schedule(episode)


def test_weibull_is_reproducible_across_resume_and_call_order():
    episodes = [0, 1, 7, 100003]
    schedule = make_wind_schedule("weibull", seed=np.int64(9))
    expected = {ep: schedule(ep) for ep in episodes}
    resumed = make_wind_schedule("weibull:9", seed=88)
    assert {ep: resumed(ep) for ep in reversed(episodes)} == expected
    assert any(make_wind_schedule("weibull", seed=10)(ep) != expected[ep] for ep in episodes)
    assert all(WS_BOUNDS[0] <= value[0] <= WS_BOUNDS[1] and value[1] is None
               for value in expected.values())


def test_cycle_boundaries_and_floris_preset_direction():
    schedule = make_wind_schedule("cycle:6,8,10")
    assert [schedule(ep) for ep in (0, 2, 3, 100003)] == [(6., None), (10., None), (6., None), (8., None)]
    assert make_wind_schedule("cycle:8")(np.int64(10)) == (8., None)
    assert make_wind_schedule("preset:veer", backend="floris")(0) == (8., 240.)
