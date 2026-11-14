"""Host contracts for applied inflow, world-space yaw and solver-grid placement."""
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.scene import load_scene
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl_blender.animation import KinematicState
from wfrl_blender import backend_inflow
from wfrl_blender.live_scene import snapshot_scene
from wfrl_blender.wind_coordinates import world_vertices
from wfcrl.fastfarm_direction import rotate_layout


def snapshot(direction=300.):
    scene = load_scene(Path(__file__).resolve().parents[2]/'scenes/turb3_row.yaml')
    world = np.column_stack((scene.xcoords, scene.ycoords, np.zeros(scene.n)))
    solver, transform = rotate_layout(world, direction)
    m = dict(yaw_measured=np.array([0., 10., -10.]), yaw_command=np.array([0., 10., -10.]),
             pitch=np.zeros(3), wind_speed=np.full(3, 8.), wind_direction=np.full(3, direction),
             wind_conditions={'application': {'wind_direction': {'status': 'applied', 'value': direction}},
                              'coordinates': {'transform': transform}})
    snap = SimpleNamespace(ctx={'t': 10.}, measure=m, step=1, farm_power=float('nan'), reward=0.)
    return scene, solver, SnapshotAdapter(scene, 'ds-host').encode(snap)


def test_applied_direction_overrides_scene_draft_and_keeps_world_positions():
    original, solver, payload = snapshot()
    scene = snapshot_scene(payload)
    assert original.wind_direction == 270.
    assert scene.wind_direction_deg == 300.
    assert [t.x_m for t in scene.turbines] == [0., 504., 1008.]
    assert [t.y_m for t in scene.turbines] == [0., 0., 0.]
    assert solver[-1, 1] == pytest.approx(252.)
    display = {'wfrl_scene_kind': 'live'}
    assert backend_inflow.record(display, payload)
    assert backend_inflow.sample(backend_inflow.history(display), 10.)[1] == pytest.approx(-math.pi/6)
    assert display['wfrl_wind_direction_deg'] == 300.


def test_yaw_geometry_adds_world_offset_but_telemetry_keeps_solver_angles():
    _, _, payload = snapshot()
    state = KinematicState(clock=lambda: 0.)
    state.apply_snapshot(payload)
    objects = {f'WFRL.Turbine.T{i}.YawRoot': SimpleNamespace(rotation_euler=SimpleNamespace(z=0.))
               for i in (1, 2, 3)}
    state.apply_objects(objects)
    for i, expected in enumerate((-30., -20., -40.), 1):
        assert objects[f'WFRL.Turbine.T{i}.YawRoot'].rotation_euler.z == pytest.approx(math.radians(expected))
    assert payload['turbines'][1]['channels']['yaw']['value'] == 10.
    assert payload['turbines'][1]['channels']['yaw_command']['value'] == 10.


@pytest.mark.parametrize('direction', [0., 90., 180., 270., 300.])
def test_solver_grid_points_return_to_unrotated_world(direction):
    scene, solver, payload = snapshot(direction)
    display = {'wfrl_scene_kind': 'live'}
    backend_inflow.record(display, payload)
    actual = world_vertices(solver.tolist(), display)
    expected = np.column_stack((scene.xcoords, scene.ycoords, np.zeros(scene.n)))
    np.testing.assert_allclose(actual, expected, atol=1e-10)
    vertices = [(1., 2., 3.)]
    assert world_vertices(vertices, {}) is vertices


def test_missing_yaw_is_not_replaced_by_an_inflow_heading():
    _, _, payload = snapshot()
    payload['turbines'][0]['channels']['yaw'].update(value=None, validity='unsupported')
    state = KinematicState(clock=lambda: 0.)
    state.apply_snapshot(payload)
    obj = SimpleNamespace(rotation_euler=SimpleNamespace(z=1.))
    state.apply_objects({'WFRL.Turbine.T1.YawRoot': obj})
    assert obj.rotation_euler.z == 1.


def test_inflow_arrows_follow_episode_direction_without_rebuilding_geometry():
    class Display(dict):
        pass
    scene = Display(wfrl_scene_kind='live')
    shaft = SimpleNamespace(rotation_euler=[0., 0., 0.])
    head = SimpleNamespace(rotation_euler=[0., math.pi/2, 0.], location=(102., -72., 1.))
    scene.objects = {'WFRL.Inflow.Arrow1.Shaft': shaft, 'WFRL.Inflow.Arrow1.Head': head}
    _, _, payload = snapshot()
    backend_inflow.record(scene, payload)
    assert shaft.rotation_euler[2] == pytest.approx(-math.pi/6)
    assert head.location == pytest.approx((60.+42.*math.cos(math.pi/6), -93., 1.))
    _, _, payload = snapshot(270.)
    backend_inflow.record(scene, payload)
    assert shaft.rotation_euler[2] == 0.
    assert head.location == (102., -72., 1.)
