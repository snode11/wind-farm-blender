"""SafetyLimiter commands, diagnostics, and duty budgets without a solver."""

import numpy as np
import pytest

from wfrl.safety import (
    ACTUATOR_RATE,
    DUTY_LIMIT,
    RATED_RPM,
    RATED_TORQUE,
    RPM_ALARM,
    STEP_BOUND,
    TORQUE_ENV_HI,
    TORQUE_ENV_LO,
    SafetyLimiter,
    baseline_gen_torque,
)
from wfrl.scene.schema import Scene, Turbine


def _scene(controls=("yaw",), dt=3.0):
    return Scene(
        name="safety_limiter_test",
        backend="fastfarm",
        dt=dt,
        turbines=[Turbine("W1", 0.0, 0.0), Turbine("W2", 630.0, 0.0),
                  Turbine("W3", 1260.0, 0.0)],
        wind_speed=8.0,
        controls=list(controls),
    ).validate()


@pytest.fixture
def torque_measure():
    rpm = np.full(3, 9.0)
    return {"rotor_speed": rpm, "torque": baseline_gen_torque(rpm)}


def test_probe_reference_values():
    assert RATED_RPM == pytest.approx(11.979, abs=0.01)
    assert RATED_TORQUE == pytest.approx(43528.8, abs=1.0)
    assert 20000.0 / RATED_TORQUE < 0.5
    assert 18000.0 < baseline_gen_torque(np.array([9.0]))[0] < 21000.0


@pytest.mark.parametrize("value, expected", [
    (2.0, [2.0, 2.0, 2.0]),
    ([2.0], [2.0, 2.0, 2.0]),
    ([[2.0, -1.0, 0.0]], [2.0, -1.0, 0.0]),
])
def test_apply_normalizes_commands_and_zeros_omitted_controls(value, expected):
    limiter = SafetyLimiter(_scene(("yaw", "pitch", "torque")))
    limiter.state["pitch"][:] = 10.0
    limiter.state["torque"][:] = 100.0

    command, events = limiter.apply({"yaw": value, "pitch": None, "unused": np.nan})

    assert list(command) == ["yaw", "pitch", "torque"]
    np.testing.assert_array_equal(command["yaw"], expected)
    np.testing.assert_array_equal(limiter.state["yaw"], expected)
    for control in ("pitch", "torque"):
        np.testing.assert_array_equal(command[control], np.zeros(3))
    np.testing.assert_array_equal(limiter.state["pitch"], np.full(3, 10.0))
    np.testing.assert_array_equal(limiter.state["torque"], np.full(3, 100.0))
    assert events == []


@pytest.mark.parametrize("value", [[], [1.0, 2.0], [1.0, 2.0, 3.0, 4.0]])
def test_apply_rejects_command_length_mismatch(value):
    limiter = SafetyLimiter(_scene())
    with pytest.raises(ValueError, match=rf"控制量 yaw 长度 {len(value)} != 机组数 3"):
        limiter.apply({"yaw": value})


@pytest.mark.parametrize("control", ["yaw", "pitch", "torque"])
def test_apply_guards_nan_and_infinities(control, torque_measure):
    limiter = SafetyLimiter(_scene((control,)))
    requested = np.array([np.nan, np.inf, -np.inf])

    command, events = limiter.apply({control: requested}, torque_measure)

    np.testing.assert_array_equal(command[control], np.zeros(3))
    np.testing.assert_array_equal(limiter.state[control], np.zeros(3))
    assert len(events) == 3
    for event, turbine, value in zip(events, ("W1", "W2", "W3"), requested):
        assert (event.step, event.turbine, event.control, event.rule) == (
            1, turbine, control, "非有限值")
        assert event.blocked
        assert event.applied == 0.0
        if np.isnan(value):
            assert np.isnan(event.requested)
        else:
            assert event.requested == value
    np.testing.assert_array_equal(requested, [np.nan, np.inf, -np.inf])


def test_apply_combines_nan_guard_and_step_clipping():
    limiter = SafetyLimiter(_scene())
    command, events = limiter.apply({"yaw": [12.0, np.nan, -30.0]})

    np.testing.assert_array_equal(command["yaw"], [5.0, 0.0, -5.0])
    assert [(e.turbine, e.rule, e.applied, e.severity) for e in events] == [
        ("W2", "非有限值", 0.0, "block"),
        ("W1", "单步增量越界", 5.0, "warn"),
        ("W3", "单步增量越界", -5.0, "warn"),
    ]


@pytest.mark.parametrize("control, bound, initial", [
    ("yaw", 5.0, 0.0), ("pitch", 1.0, 10.0), ("torque", 1000.0, 0.0),
])
def test_apply_clips_both_step_limits(control, bound, initial, torque_measure):
    limiter = SafetyLimiter(_scene((control,)))
    limiter.state[control][:] = initial
    requested = np.array([2.0 * bound, -2.0 * bound, bound])

    command, events = limiter.apply({control: requested}, torque_measure)

    expected = np.array([bound, -bound, bound])
    np.testing.assert_array_equal(command[control], expected)
    np.testing.assert_array_equal(limiter.state[control], initial + expected)
    assert [(e.turbine, e.rule, e.requested, e.applied, e.severity) for e in events] == [
        ("W1", "单步增量越界", 2.0 * bound, bound, "warn"),
        ("W2", "单步增量越界", -2.0 * bound, -bound, "warn"),
    ]
    np.testing.assert_array_equal(requested, [2.0 * bound, -2.0 * bound, bound])


@pytest.mark.parametrize("control, lower, upper, step", [
    ("yaw", -40.0, 40.0, 5.0),
    ("pitch", 0.0, 45.0, 1.0),
    ("torque", -20000.0, 20000.0, 1000.0),
])
def test_apply_respects_state_limits_and_allows_reversal(
    control, lower, upper, step, torque_measure,
):
    limiter = SafetyLimiter(_scene((control,)))
    middle = (lower + upper) / 2.0
    limiter.state[control][:] = [upper - step / 2.0, lower + step / 2.0, middle]
    requested = np.array([step, -step, 0.0])

    command, events = limiter.apply({control: requested}, torque_measure)
    np.testing.assert_array_equal(command[control], [step / 2.0, -step / 2.0, 0.0])
    np.testing.assert_array_equal(limiter.state[control], [upper, lower, middle])
    assert [(e.turbine, e.rule, e.requested, e.applied, e.severity) for e in events] == [
        ("W1", "累加值越界", step, step / 2.0, "warn"),
        ("W2", "累加值越界", -step, -step / 2.0, "warn"),
    ]

    command, events = limiter.apply({control: requested}, torque_measure)
    np.testing.assert_array_equal(command[control], np.zeros(3))
    assert [(e.turbine, e.rule, e.applied) for e in events] == [
        ("W1", "累加值越界", 0.0), ("W2", "累加值越界", 0.0),
    ]

    command, events = limiter.apply({control: -requested}, torque_measure)
    np.testing.assert_array_equal(command[control], -requested)
    np.testing.assert_array_equal(limiter.state[control], [upper - step, lower + step, middle])
    assert events == []


@pytest.mark.parametrize("direction", [-1.0, 1.0])
def test_repeated_yaw_commands_saturate_at_forty_degrees(direction):
    limiter = SafetyLimiter(_scene())
    for step in range(1, 10):
        command, events = limiter.apply({"yaw": direction * 5.0})
        np.testing.assert_array_equal(
            limiter.state["yaw"], np.full(3, direction * min(5.0 * step, 40.0)))
        if step <= 8:
            assert events == []
        else:
            np.testing.assert_array_equal(command["yaw"], np.zeros(3))
            assert len(events) == 3
            assert all(e.rule == "累加值越界" and e.step == 9 for e in events)


@pytest.mark.parametrize("control", ["yaw", "pitch"])
@pytest.mark.parametrize("enforce", [False, True], ids=["report-only-default", "enforced"])
def test_duty_reports_or_zeros_nonzero_commands(control, enforce):
    limiter = SafetyLimiter(_scene((control,)))
    limiter.state[control][:] = 10.0
    requested = np.array([0.25, -0.25, 0.0])
    measure = {"duty": {control: [0.1, 0.5, 0.5]}}
    options = {"enforce_duty": True} if enforce else {}

    command, events = limiter.apply({control: requested}, measure, **options)

    expected = np.zeros(3) if enforce else requested
    np.testing.assert_array_equal(command[control], expected)
    assert [(e.turbine, e.rule, e.requested, e.applied, e.blocked) for e in events] == [
        ("W1", "执行机构占空比清零", 0.25, expected[0], True),
        ("W2", "执行机构占空比清零", -0.25, expected[1], True),
    ]
    if enforce:
        np.testing.assert_array_equal(limiter.state[control], np.full(3, 10.0))


@pytest.mark.parametrize("control, step", [("yaw", 5.0), ("pitch", 1.0)])
def test_duty_uses_existing_budget_with_inclusive_threshold(control, step):
    limiter = SafetyLimiter(_scene((control,)))
    measure = {"duty": {control: [0.0, np.nextafter(0.1, 0.0), 0.1]}}

    command, events = limiter.apply({control: step}, measure, enforce_duty=True)

    np.testing.assert_array_equal(command[control], [step, step, 0.0])
    assert [(e.turbine, e.rule) for e in events] == [("W3", "执行机构占空比清零")]


def test_duty_matches_probe_accumulator_for_forty_steps():
    limiter = SafetyLimiter(_scene())
    accumulator = np.zeros(3)
    first_zero = first_event = None
    for step in range(1, 41):
        duty = accumulator / ACTUATOR_RATE["yaw"] / step / 3.0
        expected = np.full(3, 4.0)
        expected[duty >= DUTY_LIMIT] = 0.0
        command, events = limiter.apply(
            {"yaw": np.full(3, 4.0)}, {"duty": {"yaw": duty}}, enforce_duty=True)

        np.testing.assert_array_equal(command["yaw"], expected)
        blocked = [e for e in events if e.rule == "执行机构占空比清零"]
        assert [e.turbine for e in blocked] == [
            f"W{i + 1}" for i in np.flatnonzero(expected == 0.0)]
        assert all(e.step == step and e.blocked and e.applied == 0.0 for e in blocked)
        if first_zero is None and np.any(expected == 0.0):
            first_zero = step
        if first_event is None and blocked:
            first_event = step
        accumulator += np.abs(expected)

    assert first_zero == first_event == 2
    np.testing.assert_array_equal(limiter.state["yaw"], accumulator)
    assert limiter.summary() == [("执行机构占空比清零", 117)]


@pytest.mark.parametrize("measure", [None, {}, {"duty": None}, {"duty": {}},
                                     {"duty": {"pitch": [0.5, 0.5, 0.5]}}])
def test_budget_is_unlimited_without_matching_duty(measure):
    limiter = SafetyLimiter(_scene())
    np.testing.assert_array_equal(limiter.budget(measure, "yaw"), np.full(3, np.inf))


@pytest.mark.parametrize("control", ["yaw", "pitch"])
def test_budget_distinguishes_below_at_and_above_limit(control):
    limiter = SafetyLimiter(_scene((control,)))
    measure = {"duty": {control: [np.nextafter(0.1, 0.0), 0.1, 0.5]}}
    np.testing.assert_array_equal(limiter.budget(measure, control), [np.inf, 0.0, 0.0])


def test_torque_has_no_duty_budget_or_duty_events(torque_measure):
    limiter = SafetyLimiter(_scene(("torque",)))
    torque_measure["duty"] = {"torque": np.ones(3)}

    command, events = limiter.apply({"torque": 300.0}, torque_measure, enforce_duty=True)

    np.testing.assert_array_equal(limiter.budget(torque_measure, "torque"), np.full(3, np.inf))
    np.testing.assert_array_equal(command["torque"], np.full(3, 300.0))
    assert events == []


def test_pitch_reports_baseline_override_without_changing_command():
    limiter = SafetyLimiter(_scene(("yaw", "pitch")))
    command, events = limiter.apply(
        {"yaw": 0.0, "pitch": 0.8}, {"pitch_meas": [6.5, 0.0, np.nan]})

    np.testing.assert_allclose(command["pitch"], np.full(3, 0.8))
    np.testing.assert_allclose(limiter.state["pitch"], np.full(3, 0.8))
    assert len(events) == 1
    event = events[0]
    assert (event.turbine, event.control, event.rule, event.severity, event.blocked) == (
        "W1", "pitch", "指令被基线顶替", "info", False)
    assert event.requested == pytest.approx(0.8)
    assert event.applied == 6.5


def test_pitch_semantics_uses_absolute_command_and_tolerance():
    limiter = SafetyLimiter(_scene(("pitch",)))
    limiter.state["pitch"][:] = 10.0
    command, events = limiter.apply(
        {"pitch": 0.5}, {"pitch_meas": [10.55, 10.5501, 10.25]})

    np.testing.assert_array_equal(command["pitch"], np.full(3, 0.5))
    np.testing.assert_array_equal(limiter.state["pitch"], np.full(3, 10.5))
    assert [(e.turbine, e.rule, e.requested, e.applied) for e in events] == [
        ("W2", "指令被基线顶替", 10.5, 10.5501),
    ]


@pytest.mark.parametrize("measure", [None, {}, {"pitch_meas": [np.nan, np.inf, -np.inf]},
                                     {"pitch_meas": [0.0]}])
def test_pitch_semantics_skips_missing_measurements(measure):
    limiter = SafetyLimiter(_scene(("pitch",)))
    command, events = limiter.apply({"pitch": 0.8}, measure)
    np.testing.assert_allclose(command["pitch"], np.full(3, 0.8))
    assert events == []


@pytest.mark.parametrize("direction, rule", [
    (1.0, "转矩超运行上限（防失速）"), (-1.0, "转矩低于运行下限（防超速）"),
])
def test_torque_clips_to_both_sides_of_vs_envelope(direction, rule, torque_measure):
    limiter = SafetyLimiter(_scene(("torque",)))
    baseline = torque_measure["torque"].copy()
    edge = baseline * ((1.0 + TORQUE_ENV_HI) if direction > 0 else (1.0 - TORQUE_ENV_LO))
    torque_measure["torque"] = edge - direction * 200.0

    command, events = limiter.apply({"torque": direction * 1000.0}, torque_measure)

    np.testing.assert_array_equal(command["torque"], np.full(3, direction * 200.0))
    np.testing.assert_allclose(torque_measure["torque"] + command["torque"], edge)
    np.testing.assert_array_equal(limiter.state["torque"], command["torque"])
    assert [(e.turbine, e.rule, e.requested, e.applied, e.severity) for e in events] == [
        (turbine, rule, direction * 1000.0, direction * 200.0, "block")
        for turbine in ("W1", "W2", "W3")
    ]


def test_torque_keeps_adjustments_inside_envelope(torque_measure):
    limiter = SafetyLimiter(_scene(("torque",)))
    requested = np.array([-300.0, 0.0, 300.0])
    command, events = limiter.apply({"torque": requested}, torque_measure)
    np.testing.assert_array_equal(command["torque"], requested)
    np.testing.assert_array_equal(limiter.state["torque"], requested)
    assert events == []


@pytest.mark.parametrize("state, correction", [(0.0, 1000.0), (19900.0, 100.0), (20000.0, 0.0)])
def test_torque_correction_respects_step_and_state_limits(state, correction, torque_measure):
    limiter = SafetyLimiter(_scene(("torque",)))
    limiter.state["torque"][:] = [state, -state, 0.0]
    torque_measure["torque"][:2] = [0.0, 40000.0]

    command, events = limiter.apply({"torque": 0.0}, torque_measure)

    np.testing.assert_array_equal(command["torque"], [correction, -correction, 0.0])
    np.testing.assert_array_equal(
        limiter.state["torque"], [state + correction, -state - correction, 0.0])
    assert [(e.turbine, e.rule, e.requested, e.applied, e.severity) for e in events] == [
        ("W1", "转矩低于运行下限（防超速）", 0.0, correction, "warn"),
        ("W2", "转矩超运行上限（防失速）", 0.0, -correction, "warn"),
    ]
    assert all("尚未进入包线" in e.detail for e in events)


def test_torque_command_and_state_use_float32_representable_deltas(torque_measure):
    limiter = SafetyLimiter(_scene(("torque",)))
    requested = np.array([0.1, -0.1, 0.2])
    command, events = limiter.apply({"torque": requested}, torque_measure)
    expected = requested.astype(np.float32).astype(float)
    np.testing.assert_array_equal(command["torque"], expected)
    np.testing.assert_array_equal(limiter.state["torque"], expected)
    assert events == []


@pytest.mark.parametrize("measure", [
    None,
    {"rotor_speed": [9.0, 9.0, 9.0], "torque": [0.0, 40000.0, 0.0]},
])
def test_nonfinite_torque_stays_zero_outside_envelope(measure):
    limiter = SafetyLimiter(_scene(("torque",)))
    initial = np.array([20000.0, -20000.0, 123.0])
    limiter.state["torque"][:] = initial

    command, events = limiter.apply({"torque": [np.nan, np.inf, -np.inf]}, measure)

    np.testing.assert_array_equal(command["torque"], np.zeros(3))
    np.testing.assert_array_equal(limiter.state["torque"], initial)
    assert len(events) == 3
    for i, event in enumerate(events):
        assert (event.turbine, event.rule, event.blocked) == (
            f"W{i + 1}", "非有限值", True)
        assert event.applied == command["torque"][i] == limiter.state["torque"][i] - initial[i]
    assert limiter.events == events
    assert limiter.summary() == [("非有限值", 3)]


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_nonfinite_torque_does_not_disable_valid_turbine_corrections(invalid):
    limiter = SafetyLimiter(_scene(("torque",)))
    command, events = limiter.apply(
        {"torque": [invalid, 0.0, 0.0]},
        {"rotor_speed": [9.0, 9.0, 9.0], "torque": [0.0, 0.0, 40000.0]},
    )

    np.testing.assert_array_equal(command["torque"], [0.0, 1000.0, -1000.0])
    np.testing.assert_array_equal(limiter.state["torque"], command["torque"])
    assert [(e.turbine, e.rule, e.applied) for e in events] == [
        ("W1", "非有限值", 0.0),
        ("W2", "转矩低于运行下限（防超速）", 1000.0),
        ("W3", "转矩超运行上限（防失速）", -1000.0),
    ]


@pytest.mark.parametrize("capacity", [-1, -2000])
def test_max_events_rejects_negative_capacity(capacity):
    with pytest.raises(ValueError, match="max_events"):
        SafetyLimiter(_scene(), max_events=capacity)


@pytest.mark.parametrize("capacity", [0, 1, 4])
def test_max_events_keeps_newest_events_and_summary_keeps_all_counts(capacity):
    limiter = SafetyLimiter(_scene(), max_events=capacity)
    assert limiter.summary() == []
    assert limiter.recent() == []
    emitted = []
    for step in range(1, 4):
        _, events = limiter.apply({"yaw": [12.0, np.nan, -30.0]})
        assert len(events) == 3
        assert all(e.step == step for e in events)
        emitted.extend(events)
        assert len(limiter.events) <= capacity
        assert limiter.events == (emitted[-capacity:] if capacity else [])
        if capacity == 0:
            assert limiter.recent() == []

    assert limiter.summary() == [("单步增量越界", 6), ("非有限值", 3)]
    summary = limiter.summary()
    summary.clear()
    assert limiter.summary() == [("单步增量越界", 6), ("非有限值", 3)]


def test_recent_returns_chronological_tail_without_exposing_list():
    limiter = SafetyLimiter(_scene())
    emitted = []
    for _ in range(10):
        _, events = limiter.apply({"yaw": np.nan})
        emitted.extend(events)

    assert limiter.recent() == emitted[-20:]
    assert limiter.recent(5) == emitted[-5:]
    assert limiter.recent(1) == emitted[-1:]
    assert limiter.recent(0) == []
    assert limiter.events == emitted
    assert limiter.recent(100) == emitted
    recent = limiter.recent(5)
    recent.clear()
    assert limiter.recent(5) == emitted[-5:]


@pytest.mark.parametrize("dt, yaw, pitch", [(3.0, 0.09, 2.4), (60.0, 1.8, 48.0)])
def test_sustainable_scales_with_scene_dt(dt, yaw, pitch):
    limiter = SafetyLimiter(_scene(("yaw", "pitch", "torque"), dt=dt))
    assert limiter.sustainable("yaw") == pytest.approx(yaw)
    assert limiter.sustainable("pitch") == pytest.approx(pitch)
    assert limiter.sustainable("torque") == np.inf
    if dt == 3.0:
        assert 5.0 / limiter.sustainable("yaw") == pytest.approx(55.6, abs=0.5)


def test_reachable_includes_first_step_allowance():
    limiter = SafetyLimiter(_scene(("yaw", "pitch", "torque")))
    assert limiter.reachable("yaw", 128) == pytest.approx(16.52)
    assert limiter.reachable("yaw", 128) < 20.0
    assert limiter.reachable("pitch", 128) == pytest.approx(308.2)
    assert limiter.reachable("torque", 128) == np.inf


@pytest.mark.parametrize("n_steps", [None, 128])
def test_notes_describes_control_budgets_and_optional_horizon(n_steps):
    limiter = SafetyLimiter(_scene(("torque", "pitch", "yaw")))
    lines = limiter.notes(n_steps=n_steps)
    assert len(lines) == 3
    assert lines[0].startswith("torque: 不受执行机构占空比约束")
    assert lines[1].startswith("pitch: 动作空间 ±1/步")
    assert "占空比可持续量 2.4/步" in lines[1]
    assert "持续满幅动作必被清零" not in lines[1]
    assert lines[2].startswith("yaw: 动作空间 ±5/步")
    assert "可持续量仅 0.09/步" in lines[2]
    assert "持续满幅动作必被清零" in lines[2]
    if n_steps is None:
        assert all("步回合内" not in line for line in lines)
    else:
        assert "128 步回合内最多累积 308" in lines[1]
        assert "128 步回合内最多累积 16.5" in lines[2]
        assert "步回合内" not in lines[0]


@pytest.mark.parametrize("dt, warned_controls", [
    (1.0, {"yaw", "pitch"}),
    (3.0, {"yaw"}),
    (200.0, set()),
])
def test_notes_only_warns_when_full_step_exceeds_sustainable_amount(dt, warned_controls):
    limiter = SafetyLimiter(_scene(("yaw", "pitch"), dt=dt))
    for control, line in zip(limiter.controls, limiter.notes()):
        assert ("持续满幅动作必被清零" in line) == (control in warned_controls)
        if control not in warned_controls:
            assert limiter.sustainable(control) >= STEP_BOUND[control]
            assert "满幅动作不超过占空比可持续量" in line
            assert "差 " not in line


def test_notes_does_not_warn_when_full_step_equals_sustainable_amount(monkeypatch):
    limiter = SafetyLimiter(_scene(("pitch",)))
    monkeypatch.setitem(STEP_BOUND, "pitch", limiter.sustainable("pitch"))

    line, = limiter.notes()

    assert "持续满幅动作必被清零" not in line
    assert "满幅动作不超过占空比可持续量" in line


def test_monitor_uses_interval_peak_and_its_time_even_with_low_latest_speed():
    limiter = SafetyLimiter(_scene())
    limiter.apply({"yaw": 0.0})
    state = limiter.state["yaw"].copy()

    events = limiter.monitor({
        "rotor_speed": [12.1, 12.1, 12.1],
        "rotor_speed_interval_max": [18.0, 17.0, RPM_ALARM],
        "rotor_speed_interval_max_time": [109.5, 110.75, 110.0],
        "rotor_speed_interval_samples": [12, 1, 12],
        "torque": [0.0, 0.0, 0.0],
        "power": [2.0, 2.0, 2.0],
    })

    assert [(e.step, e.turbine, e.control, e.rule, e.requested, e.applied, e.blocked)
            for e in events] == [
        (1, "W1", "rotor_speed", "超速", 18.0, 18.0, True),
        (1, "W2", "rotor_speed", "超速", 17.0, 17.0, True),
    ]
    assert "峰值时刻 t=109.50 s" in events[0].detail
    assert "峰值时刻 t=110.75 s" in events[1].detail
    assert limiter.events == events
    assert limiter.summary() == [("超速", 2)]
    np.testing.assert_array_equal(limiter.state["yaw"], state)


@pytest.mark.parametrize("interval", [
    {},
    {"rotor_speed_interval_max": [99.0, 99.0, 99.0],
     "rotor_speed_interval_max_time": [109.5, 109.5, 109.5],
     "rotor_speed_interval_samples": [0, 0, 0]},
], ids=["no-interval-fields", "zero-samples"])
def test_monitor_falls_back_to_latest_without_interval_samples(interval):
    limiter = SafetyLimiter(_scene())
    speed = RPM_ALARM + 1.0
    events = limiter.monitor({
        **interval,
        "rotor_speed": [speed, RPM_ALARM, np.nextafter(RPM_ALARM, 0.0)],
    })

    assert [(e.turbine, e.rule, e.requested, e.applied, e.blocked) for e in events] == [
        ("W1", "超速", speed, speed, True),
    ]
    assert "峰值时刻" not in events[0].detail
    assert limiter.events == events
    assert limiter.summary() == [("超速", 1)]


def test_monitor_falls_back_to_latest_when_peak_is_nonfinite():
    limiter = SafetyLimiter(_scene())
    events = limiter.monitor({
        "rotor_speed": [14.0, 15.0, 16.0],
        "rotor_speed_interval_max": [np.nan, np.inf, -np.inf],
        "rotor_speed_interval_max_time": [109.5, 109.5, 109.5],
        "rotor_speed_interval_samples": [1, 1, 1],
    })

    assert [(e.turbine, e.requested, e.applied) for e in events] == [
        ("W1", 14.0, 14.0), ("W2", 15.0, 15.0), ("W3", 16.0, 16.0),
    ]
    assert all("峰值时刻" not in e.detail for e in events)


def test_monitor_prefers_valid_peak_with_nonfinite_time_and_missing_latest():
    limiter = SafetyLimiter(_scene())
    events = limiter.monitor({
        "rotor_speed": [99.0, np.nan],
        "rotor_speed_interval_max": [RPM_ALARM, 18.0, 19.0],
        "rotor_speed_interval_max_time": [np.nan, np.inf, -np.inf],
        "rotor_speed_interval_samples": [1, 1, 1],
    })

    assert [(e.turbine, e.requested, e.applied) for e in events] == [
        ("W2", 18.0, 18.0), ("W3", 19.0, 19.0),
    ]
    assert all("峰值时刻" not in e.detail for e in events)


@pytest.mark.parametrize("field, expected, timed", [
    ("rotor_speed_interval_samples", [18.0, 15.0, 16.0], {"W1"}),
    ("rotor_speed_interval_max", [18.0, 15.0, 16.0], {"W1"}),
    ("rotor_speed_interval_max_time", [18.0, 19.0, 20.0], {"W1"}),
    ("rotor_speed", [14.0], set()),
])
def test_monitor_checks_each_array_length_before_indexing(field, expected, timed):
    limiter = SafetyLimiter(_scene())
    measure = {
        "rotor_speed": [14.0, 15.0, 16.0],
        "rotor_speed_interval_max": [18.0, 19.0, 20.0],
        "rotor_speed_interval_max_time": [109.5, 109.5, 109.5],
        "rotor_speed_interval_samples": [1, 1, 1],
    }
    measure[field] = measure[field][:1]
    if field == "rotor_speed":
        measure["rotor_speed_interval_samples"] = [0, 0, 0]

    events = limiter.monitor(measure)

    assert [(e.turbine, e.requested, e.applied) for e in events] == [
        (f"W{i + 1}", speed, speed) for i, speed in enumerate(expected)
    ]
    assert {e.turbine for e in events if "峰值时刻 t=109.50 s" in e.detail} == timed


@pytest.mark.parametrize("measure", [
    {},
    {"rotor_speed": [], "rotor_speed_interval_max": [],
     "rotor_speed_interval_max_time": [], "rotor_speed_interval_samples": []},
    {"rotor_speed": [np.nan, np.nan, np.nan]},
    {"power": [2.0, 2.0, 2.0], "torque": [0.0, 0.0, 0.0]},
    {"rotor_speed": [np.nan, np.nan, np.nan],
     "rotor_speed_interval_max": [np.nan, np.nan, np.nan],
     "rotor_speed_interval_max_time": [np.nan, np.nan, np.nan],
     "rotor_speed_interval_samples": [0, 0, 0],
     "power": [2.0, 2.0, 2.0], "torque": [0.0, 0.0, 0.0]},
], ids=["missing", "empty-arrays", "nan", "missing-with-power-and-torque",
        "nan-with-interval-fields"])
def test_monitor_missing_rotor_speed_produces_no_events(measure):
    limiter = SafetyLimiter(_scene())
    _, previous = limiter.apply({"yaw": np.nan})
    summary = limiter.summary()

    assert limiter.monitor(measure) == []
    assert limiter.events == previous
    assert limiter.summary() == summary
