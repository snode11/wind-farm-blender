import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.blender_bridge.messages import FrameDecoder, encode_message
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.blender_bridge.training_progress import interactive_progress
from wfrl.scene.power import farm_power_total
from wfrl.scene.runtime import SceneRuntime
from wfrl.scene.schema import Scene, SensorSpec, Turbine


INVALID_MEASUREMENTS = [
    pytest.param({}, id="absent"),
    pytest.param({"power": None}, id="none"),
    pytest.param({"power": []}, id="empty"),
    pytest.param({"power": [3.0]}, id="too-few-turbines"),
    pytest.param({"power": [1.0, 2.0, 3.0]}, id="too-many-turbines"),
    pytest.param({"power": 3.0}, id="scalar"),
    pytest.param({"power": [[1.0], [2.0]]}, id="matrix"),
    pytest.param({"power": [np.nan, np.nan]}, id="all-missing"),
    pytest.param({"power": [3.0, np.nan]}, id="partly-missing"),
    pytest.param({"power": [3.0, None]}, id="partly-none"),
    pytest.param({"power": np.ma.array([3.0, 2.0], mask=[False, True])}, id="masked-reading"),
    pytest.param({"power": [3.0, np.inf]}, id="positive-infinity"),
    pytest.param({"power": [-np.inf, 3.0]}, id="negative-infinity"),
    pytest.param({"power": [1e308, 1e308]}, id="sum-overflow"),
]


def _scene(count=2):
    return Scene(
        name="power-validity",
        turbines=[Turbine(f"T{i + 1}", i * 630.0, 0.0) for i in range(count)],
        sensors=[],
    )


def _snapshot(runtime, frame):
    return SimpleNamespace(
        step=runtime._t,
        iters_done=0,
        phase="sampling",
        measure=runtime.last_measure,
        ctx=runtime.last_ctx,
        frame=frame,
        farm_power=frame["_meta"]["farm_power"],
        reward=0.0,
    )


def _round_trip(payload, kind="snapshot"):
    message = dict(
        protocol_version=1, type=kind, session_id="power-test", sequence=1,
        payload=payload,
    )
    framed = encode_message(message)

    def reject_constant(value):
        pytest.fail(f"Nonstandard JSON numeric constant: {value}")

    assert json.loads(framed[4:], parse_constant=reject_constant) == message
    assert FrameDecoder().feed(framed) == [message]
    return message["payload"]


@pytest.mark.parametrize("raw,expected", [
    ({}, None), ({"power": None}, None), ({"power": []}, None),
    ({"power": [True]}, None), ({"power": [2., 999.]}, None),
    ({"power": [[2.]]}, None), ({"power": ["2"]}, None),
    ({"power": [float('nan')]}, None), ({"power": [float('inf')]}, None),
    ({"power": np.ma.array([2.], mask=[True])}, None),
    ({"power": [0.]}, 0.), ({"power": 0}, 0.),
    ({"power": np.array([2.], dtype=np.float32)}, 2.),
])
def test_raw_infos_remain_valid_or_invalid_through_driver_runtime_and_wire(raw, expected):
    from wfrl.fastfarm_driver import FastFarmDriver
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.n, driver.agents, driver._idx = 2, ['a', 'b'], {'a': 0, 'b': 1}
    driver.env = SimpleNamespace(infos={'a': {'power': [3.]}, 'b': raw},
        observe=lambda agent: {}, mdp=SimpleNamespace(ACTUATORS_RATE={}))
    driver.controls, driver.yaw_duty_sliding = [], False
    driver.done, driver._steps = False, 0
    driver._raw_measure = lambda channel: None
    driver._duty = lambda: {'a': {}, 'b': {}}
    measure = driver._read(0.)
    scene = _scene()
    scene.sensors = [SensorSpec('power')]
    runtime = SceneRuntime(scene)
    frame = runtime._emit(measure)
    payload = _round_trip(SnapshotAdapter(scene, 'power-test').encode(_snapshot(runtime, frame)))
    reading = payload['turbines'][1]['channels']['power']
    assert reading['value'] == expected
    assert reading['validity'] == ('invalid' if expected is None else 'valid')
    assert payload['farm']['power']['value'] == (None if expected is None else 3. + expected)
    if expected is None:
        assert np.isnan(frame['power'][1])
    else:
        assert frame['power'][1] == expected


@pytest.mark.parametrize("measure", INVALID_MEASUREMENTS)
def test_incomplete_farm_power_is_invalid_through_wire_protocol(measure):
    runtime = SceneRuntime(_scene())
    frame = runtime._emit(measure)
    assert math.isnan(frame["_meta"]["farm_power"])

    payload = _round_trip(SnapshotAdapter(runtime.scene, "power-test").encode(
        _snapshot(runtime, frame)))
    power = payload["farm"]["power"]
    assert power["value"] is None
    assert power["validity"] in ({"invalid", "unsupported"} if "power" not in measure
                                 else {"invalid"})
    assert power["error"]
    assert power["unit"] == "MW"


@pytest.mark.parametrize("measure", INVALID_MEASUREMENTS)
def test_adapter_rejects_finite_aggregate_without_full_measurement_coverage(measure):
    runtime = SceneRuntime(_scene())
    snapshot = _snapshot(runtime, runtime._emit(measure))
    snapshot.farm_power = 3.0

    payload = _round_trip(SnapshotAdapter(runtime.scene, "power-test").encode(snapshot))
    assert payload["farm"]["power"]["value"] is None
    assert payload["farm"]["power"]["validity"] != "valid"


@pytest.mark.parametrize("power,total", [
    pytest.param([3.0, 2.0], 5.0, id="complete"),
    pytest.param([0.0, 0.0], 0.0, id="actual-zero-power"),
    pytest.param(np.array([1.25, 2.5], dtype=np.float32), 3.75, id="numpy-vector"),
    pytest.param(np.array([1.25, 2.5], dtype=np.longdouble), 3.75, id="numpy-longdouble-vector"),
    pytest.param([3.0], 3.0, id="single-turbine-vector"),
    pytest.param([3, 0], 3.0, id="python-integers"),
    pytest.param([np.int64(3), np.float32(2.5)], 5.5, id="mixed-numeric-scalars"),
    pytest.param(np.array([0, np.float64(2.5)], dtype=object), 2.5, id="numeric-object-array"),
    pytest.param(np.ma.array([0.0, 2.5], mask=[False, False]), 2.5, id="unmasked-numeric-array"),
])
def test_complete_power_includes_all_expected_turbines(power, total):
    runtime = SceneRuntime(_scene(len(power)))
    frame = runtime._emit({"power": power})
    assert farm_power_total(power, runtime.scene.n) == total
    assert frame["_meta"]["farm_power"] == total

    payload = _round_trip(SnapshotAdapter(runtime.scene, "power-test").encode(
        _snapshot(runtime, frame)))
    assert payload["farm"]["power"]["value"] == total
    assert payload["farm"]["power"]["validity"] == "valid"
    assert [t["channels"]["power"]["value"] for t in payload["turbines"]] == list(power)


@pytest.mark.parametrize("power,expected", [
    pytest.param([3, np.timedelta64(2, "s")], [3, None], id="mixed-timedelta-scalar"),
    pytest.param([3, np.timedelta64(2, "ns")], [3, None], id="mixed-nanosecond-scalar"),
    pytest.param(np.array([1, 2], dtype="datetime64[ns]"), [None, None], id="datetime-array"),
    pytest.param(np.array([1, 2], dtype="timedelta64[ns]"), [None, None], id="timedelta-array"),
])
def test_temporal_values_are_not_numeric_power_readings(power, expected):
    runtime = SceneRuntime(_scene())
    frame = runtime._emit({"power": power})

    assert math.isnan(farm_power_total(power, runtime.scene.n))
    assert math.isnan(frame["_meta"]["farm_power"])
    payload = _round_trip(SnapshotAdapter(runtime.scene, "power-test").encode(
        _snapshot(runtime, frame)))
    assert payload["farm"]["power"]["validity"] == "invalid"
    for turbine, value in zip(payload["turbines"], expected):
        channel = turbine["channels"]["power"]
        assert channel["value"] == value
        assert channel["validity"] == ("invalid" if value is None else "valid")


@pytest.mark.parametrize("missing", [np.nan, None])
def test_partial_missing_power_preserves_valid_individual_turbine(missing):
    runtime = SceneRuntime(_scene())
    frame = runtime._emit({"power": [3.0, missing]})
    payload = _round_trip(SnapshotAdapter(runtime.scene, "power-test").encode(
        _snapshot(runtime, frame)))

    first, second = [t["channels"]["power"] for t in payload["turbines"]]
    assert first["value"] == 3.0 and first["validity"] == "valid"
    assert second["value"] is None
    assert second["validity"] == ("unsupported" if missing is None else "invalid")
    assert payload["farm"]["power"]["value"] is None


@pytest.mark.parametrize("power,expected", [
    pytest.param([True, False], [None, None], id="boolean-vector"),
    pytest.param(["3", "2"], [None, None], id="string-vector"),
    pytest.param(np.array([True, False]), [None, None], id="numpy-booleans"),
    pytest.param(np.array(["3", "2"]), [None, None], id="numpy-strings"),
    pytest.param([3, True], [3, None], id="mixed-boolean"),
    pytest.param([3, "2"], [3, None], id="mixed-string"),
    pytest.param([np.int64(3), np.bool_(True)], [3, None], id="mixed-numpy-scalars"),
])
def test_adapter_rejects_nonnumeric_power_without_coercing_valid_turbines(power, expected):
    runtime = SceneRuntime(_scene())
    frame = runtime._emit({"power": power})
    assert math.isnan(farm_power_total(power, runtime.scene.n))
    assert math.isnan(frame["_meta"]["farm_power"])
    snapshot = _snapshot(runtime, frame)
    snapshot.farm_power = 5.0

    payload = _round_trip(SnapshotAdapter(runtime.scene, "power-test").encode(snapshot))

    assert payload["farm"]["power"]["value"] is None
    assert payload["farm"]["power"]["validity"] == "invalid"
    for turbine, value in zip(payload["turbines"], expected):
        channel = turbine["channels"]["power"]
        assert channel["value"] == value
        assert channel["validity"] == ("invalid" if value is None else "valid")
        if value is None:
            assert channel["error"]


@pytest.mark.parametrize("total", [True, "5", np.timedelta64(5, "ns"), np.datetime64(5, "ns")])
def test_adapter_rejects_nonnumeric_supplied_total_through_wire_protocol(total):
    runtime = SceneRuntime(_scene())
    snapshot = _snapshot(runtime, runtime._emit({"power": [3.0, 2.0]}))
    snapshot.farm_power = total

    payload = _round_trip(SnapshotAdapter(runtime.scene, "power-test").encode(snapshot))

    assert payload["farm"]["power"]["value"] is None
    assert payload["farm"]["power"]["validity"] == "invalid"
    assert all(t["channels"]["power"]["validity"] == "valid" for t in payload["turbines"])


@pytest.mark.parametrize("power", [[], [3.0]])
def test_malformed_count_keeps_invalid_total_when_power_sensor_reports_error(power):
    scene = _scene()
    scene.sensors = [SensorSpec("power")]
    runtime = SceneRuntime(scene)

    frame = runtime._emit({"power": power})

    assert math.isnan(frame["_meta"]["farm_power"])
    assert frame["power"]["error"]
    assert runtime.last_measure["power"] is power
    payload = _round_trip(SnapshotAdapter(scene, "power-test").encode(_snapshot(runtime, frame)))
    assert payload["farm"]["power"]["value"] is None


@pytest.mark.parametrize("power,total", [([3.0, np.nan], None), ([3.0, 2.0], 5.0)])
@pytest.mark.parametrize("enabled", [True, False])
def test_sensor_switch_does_not_change_total_coverage(power, total, enabled):
    scene = _scene()
    scene.sensors = [SensorSpec("power")]
    runtime = SceneRuntime(scene)
    runtime.set_enabled("power", enabled)

    frame = runtime._emit({"power": power})

    if total is None:
        assert math.isnan(frame["_meta"]["farm_power"])
    else:
        assert frame["_meta"]["farm_power"] == total
    assert runtime.last_measure["power"] is power
    if enabled:
        np.testing.assert_allclose(frame["power"], power, equal_nan=True)
    else:
        assert "power" not in frame
    payload = _round_trip(SnapshotAdapter(scene, "power-test").encode(_snapshot(runtime, frame)))
    assert payload["farm"]["power"]["value"] == total


@pytest.mark.parametrize("total", [np.nan, np.inf, -np.inf])
def test_adapter_never_serializes_nonfinite_supplied_total(total):
    runtime = SceneRuntime(_scene())
    snapshot = _snapshot(runtime, runtime._emit({"power": [3.0, 2.0]}))
    snapshot.farm_power = total
    payload = _round_trip(SnapshotAdapter(runtime.scene, "power-test").encode(snapshot))
    assert payload["farm"]["power"]["value"] is None
    assert payload["farm"]["power"]["validity"] == "invalid"


def test_missing_then_complete_then_missing_measurements_do_not_reuse_totals():
    runtime = SceneRuntime(_scene())
    adapter = SnapshotAdapter(runtime.scene, "power-test")
    states = []
    for measure in ({"power": [3.0, np.nan]}, {"power": [3.0, 2.0]}, {}):
        frame = runtime._emit(measure)
        power = _round_trip(adapter.encode(_snapshot(runtime, frame)))["farm"]["power"]
        states.append((power["value"], power["validity"]))
    assert states[0] == (None, "invalid")
    assert states[1] == (5.0, "valid")
    assert states[2][0] is None and states[2][1] != "valid"


def _fake_runtime(monkeypatch, measurements):
    import wfrl.scene.runtime as runtime_module

    class FakeRuntime(SceneRuntime):
        def __init__(self, scene, **kwargs):
            super().__init__(scene, **kwargs)
            self.samples = iter(measurements)
            bounds = {"yaw": (-5.0, 5.0), "pitch": (-1.0, 1.0),
                      "torque": (-1000.0, 1000.0)}
            self.driver = SimpleNamespace(
                act_bounds={control: bounds[control] for control in scene.controls},
                act_low=-5.0, act_high=5.0,
                close=lambda **kwargs: None,
            )
            self.u_inf = 8.0

        def _measurement(self, power_measure):
            return dict(
                **{control: np.zeros(self.scene.n) for control in self.scene.controls},
                wind_speed=np.full(self.scene.n, 8.0),
                wind_direction=np.full(self.scene.n, 270.0), reward=1.0,
                **power_measure,
            )

        def reset(self):
            return self._emit(self._measurement({}))

        def step(self, **kwargs):
            self._t += 1
            return self._emit(self._measurement(next(self.samples)))

    monkeypatch.setattr(runtime_module, "SceneRuntime", FakeRuntime)
    return FakeRuntime


@pytest.mark.parametrize("metadata", ["absent", None, 0.0])
def test_trainer_warmup_preserves_missing_metadata_and_actual_zero(monkeypatch, metadata):
    from wfrl.studio.trainer import Trainer

    runtime_type = _fake_runtime(monkeypatch, [{"power": [0.0, 0.0]}])

    def emit(self, measure):
        frame = SceneRuntime._emit(self, dict(measure, power=[0.0, 0.0]))
        if metadata == "absent":
            frame["_meta"].pop("farm_power")
        else:
            frame["_meta"]["farm_power"] = metadata
        return frame

    monkeypatch.setattr(runtime_type, "_emit", emit)
    snapshots = []
    trainer = Trainer(_scene(), iters=0, warmup_steps=1, on_snapshot=snapshots.append)
    try:
        trainer._train()
    finally:
        if trainer.runtime is not None:
            trainer.runtime.close()

    warmup = [s for s in snapshots if s.frame and s.phase == "warmup"]
    assert len(warmup) == 2
    for snapshot in warmup:
        if metadata == 0.0:
            assert snapshot.farm_power == 0.0
        else:
            assert math.isnan(snapshot.farm_power)
        payload = _round_trip(SnapshotAdapter(trainer.scene, "power-test").encode(snapshot))
        power = payload["farm"]["power"]
        assert power["value"] == (0.0 if metadata == 0.0 else None)
        assert power["validity"] == ("valid" if metadata == 0.0 else "invalid")


@pytest.mark.parametrize("missing", [
    pytest.param({}, id="absent"),
    pytest.param({"power": [3.0, np.nan]}, id="partial-nan"),
    pytest.param({"power": [3.0]}, id="incomplete-vector"),
    pytest.param({"power": [3, True]}, id="mixed-boolean"),
    pytest.param({"power": [3, "2"]}, id="mixed-string"),
    pytest.param({"power": np.ma.array([3.0, 2.0], mask=[False, True])}, id="masked-reading"),
])
def test_trainer_iteration_mean_requires_each_sample_and_next_iteration_recovers(monkeypatch, missing):
    from wfrl.studio.trainer import Trainer

    _fake_runtime(monkeypatch, [
        missing, {"power": [1.0, 2.0]},
        {"power": [2.0, 2.0]}, {"power": [3.0, 3.0]},
    ])
    snapshots = []
    trainer = Trainer(_scene(), iters=2, n_steps=2, warmup_steps=0,
                      on_snapshot=snapshots.append)
    monkeypatch.setattr(trainer, "_update", lambda *args: dict(vloss=0.0, explained_var=0.0))
    try:
        trainer._train()
    finally:
        if trainer.runtime is not None:
            trainer.runtime.close()

    assert len(trainer.history) == 2
    assert math.isnan(trainer.history[0]["mean_power"])
    assert trainer.history[1]["mean_power"] == 5.0
    warmup = next(s for s in snapshots if s.frame and s.phase == "warmup")
    assert math.isnan(warmup.farm_power)
    updating = [s for s in snapshots if s.phase == "updating"]
    stats = [_round_trip(interactive_progress(s, "run", 2), "training_stats")["stats"]["mean_power"]
             for s in updating]
    assert stats[0]["value"] is None and stats[0]["validity"] == "invalid"
    assert stats[1]["value"] == 5.0 and stats[1]["validity"] == "valid"
    adapter = SnapshotAdapter(trainer.scene, "power-test")
    for snapshot in snapshots:
        _round_trip(adapter.encode(snapshot))


def test_multicontrol_real_ppo_update_preserves_missing_power_statistics(monkeypatch):
    import torch
    from wfrl.studio.trainer import Trainer

    _fake_runtime(monkeypatch, [
        {"power": [3.0, np.nan]}, {"power": [1.0, 2.0]},
        {"power": [2.0, 2.0]}, {"power": [3.0, 3.0]},
    ])
    scene = _scene()
    scene.controls = ["yaw", "pitch", "torque"]
    snapshots = []
    trainer = Trainer(scene, iters=2, n_steps=2, warmup_steps=0,
                      n_epochs=1, mb_steps=2, on_snapshot=snapshots.append)
    try:
        trainer._train()
    finally:
        if trainer.runtime is not None:
            trainer.runtime.close()

    assert len(trainer.history) == 2
    assert [stats["step"] for stats in trainer.history] == [2, 4]
    assert math.isnan(trainer.history[0]["mean_power"])
    assert trainer.history[1]["mean_power"] == 5.0
    for stats in trainer.history:
        for key in ("pg", "vloss", "ent", "explained_var", "mean_reward"):
            assert math.isfinite(stats[key]), (key, stats[key])
    for network in (trainer.actor, trainer.critic):
        assert all(torch.isfinite(parameter).all().item() for parameter in network.parameters())

    updating = [snapshot for snapshot in snapshots if snapshot.phase == "updating"]
    assert len(updating) == 2
    records = [_round_trip(interactive_progress(snapshot, "run", scene.n), "training_stats")
               for snapshot in updating]
    first_power, second_power = [record["stats"]["mean_power"] for record in records]
    assert first_power["value"] is None and first_power["validity"] == "invalid"
    assert second_power["value"] == 5.0 and second_power["validity"] == "valid"
    assert all(record["stats"]["value_loss"]["validity"] == "valid" for record in records)


def test_replay_history_uses_validated_totals_for_incomplete_measurements(tmp_path, monkeypatch):
    import torch
    from wfrl.mappo_central import Actor
    from wfrl.policy_actions import PolicyActionContract
    from wfrl.studio.trainer import Trainer

    _fake_runtime(monkeypatch, [{}, {"power": [3.0]}, {"power": [2.0, 4.0]}])
    checkpoint = tmp_path / "policy.pt"
    torch.save(dict(
        obs_keys=["yaw", "wind_speed", "wind_direction"],
        obs_mean=np.zeros(3), obs_var=np.ones(3), obs_dim=3, act_dim=1,
        state_dim=3 * _scene().n,
        action_contract=PolicyActionContract.from_bounds(
            ["yaw"], {"yaw": (-5.0, 5.0)}, "tanh_scale").to_dict(),
        controls=["yaw"], actor=Actor(3, 1).state_dict(),
    ), checkpoint)
    snapshots = []
    trainer = Trainer(_scene(), ckpt_path=str(checkpoint), replay=True,
                      replay_steps=3, warmup_steps=0, on_snapshot=snapshots.append)
    try:
        trainer._replay_policy()
    finally:
        if trainer.runtime is not None:
            trainer.runtime.close()

    assert len(trainer.history) == 3
    assert math.isnan(trainer.history[0]["mean_power"])
    assert math.isnan(trainer.history[1]["mean_power"])
    assert trainer.history[2]["mean_power"] == 6.0
    sampling = [s for s in snapshots if s.phase == "sampling"]
    assert math.isnan(sampling[0].farm_power)
    assert math.isnan(sampling[1].farm_power)
    assert sampling[2].farm_power == 6.0
    adapter = SnapshotAdapter(trainer.scene, "power-test", mode="replay")
    for snapshot in snapshots:
        _round_trip(adapter.encode(snapshot))
