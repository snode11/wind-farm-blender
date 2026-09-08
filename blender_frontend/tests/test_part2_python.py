import math

from wfrl_blender.scene_model import SceneDTO
from wfrl_blender.state import DemoState, RunStatus, sample_demo, time_for_frame, END_FRAME


def test_scene_dto_preserves_real_meter_layout():
    scene = SceneDTO.from_mapping({"name": "turb3_demo", "backend": "demo", "layout": [{"id": "T1", "x": 504, "y": 31.5}], "inflow": {"speed": 8, "direction": 270}})
    assert scene.turbines[0].x_m == 504.0
    assert scene.turbines[0].y_m == 31.5
    assert scene.wind_speed_mps == 8.0


def test_pause_resume_preserves_time_and_pose():
    state = DemoState()
    assert state.reset().status == RunStatus.READY
    state.start()
    state.advance(10)
    state.pause()
    before = state.frame()
    assert state.advance(5) == before
    assert state.frame().status == RunStatus.PAUSED
    state.resume()
    assert state.elapsed_s == 10
    state.advance(1)
    assert state.elapsed_s == 11


def test_step_is_one_script_frame_and_requires_pause():
    state = DemoState()
    state.start()
    state.advance(5)
    state.step()
    assert state.elapsed_s == 5
    state.pause()
    state.step()
    assert state.elapsed_s == 5.04
    assert state.frame().status == RunStatus.PAUSED


def test_demo_is_deterministic_and_stops_exactly_at_66_seconds():
    state = DemoState()
    state.start()
    first = state.advance(10)
    state.reset()
    state.start()
    assert state.advance(10) == first
    state.advance(48)
    assert state.status == RunStatus.RUNNING
    state.advance(100)
    assert state.frame().status == RunStatus.STOPPED
    assert state.elapsed_s == 66.0
    assert END_FRAME == 1651
    assert time_for_frame(1) == 0
    assert time_for_frame(END_FRAME) == 66


def test_rotor_angle_derivative_matches_rpm_at_all_phases():
    for t in (1, 3, 4, 15, 55, 56, 58, 65):
        before = sample_demo(t - 0.0001)
        after = sample_demo(t + 0.0001)
        center = sample_demo(t)
        for index in range(3):
            rad_per_s = (after.rotor_rad[index] - before.rotor_rad[index]) / 0.0002
            assert math.isclose(rad_per_s, center.rpm[index] * math.tau / 60, abs_tol=2e-5)


def test_terminal_pose_is_stationary_feathered_and_zero_power():
    frame = sample_demo(66)
    assert frame == sample_demo(100)
    assert frame.pitch_deg == (90, 90, 90)
    assert frame.rpm == (0, 0, 0)
    assert frame.power_mw == (0, 0, 0)
    assert sample_demo(0).rotor_rad == (0, 0, 0)
