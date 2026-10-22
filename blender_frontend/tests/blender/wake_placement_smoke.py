"""Regression: animated wake stays at the hub with presentation clutter hidden."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'blender_frontend'))
import bpy
from mathutils import Vector
import wfrl_blender
from wfrl_blender import wake
from wfrl_blender.turbine_geometry import hub_position, geometry_data

wfrl_blender.register()
wfrl_blender.load_demo_scene()
scene = bpy.context.scene
for frame in (1, 301, 601):
    scene.frame_set(frame)
    for phase in (0.0, 0.25, 0.75):
        wake.update_proxy_objects(scene, phase=phase)
        bpy.context.view_layer.update()
        for tid in ('T1', 'T2', 'T3'):
            yaw = scene.objects[f'WFRL.Turbine.{tid}.YawRoot']
            hub = yaw.matrix_world @ Vector((0, 0, hub_position()[2] - geometry_data()['scalars']['TowerHt']))
            lines = [o for o in scene.objects if o.name.startswith(f'WFRL.WakeProxy.{tid}.Line')]
            starts = [o.matrix_world @ o.data.splines[0].points[0].co.xyz for o in lines]
            center = sum(starts, Vector()) / len(starts)
            assert abs(center.z - hub.z) < 1.0, (tid, 'wake center vs hub', center.z, hub.z)
            assert len(lines) == wake.PROXY_LINE_COUNT == 16
            for obj in lines:
                points = [obj.matrix_world @ p.co.xyz for p in obj.data.splines[0].points]
                assert min(p.z for p in points) > 10, 'wake crosses ground'
                local = [yaw.matrix_world.inverted() @ p for p in points]
                assert all(p.x > 0 for p in local), 'upstream counter-branch'
            rings = [o for o in scene.objects if o.name.startswith(f'WFRL.WakeProxy.{tid}.Ring')]
            assert len(rings) == wake.PROXY_RING_COUNT == 22
            for obj in rings:
                spline = obj.data.splines[0]
                assert spline.use_cyclic_u
                points = [obj.matrix_world @ p.co.xyz for p in spline.points]
                center = sum(points, Vector()) / len(points)
                assert abs(center.z - hub.z) <= 2.01
                assert min(p.z for p in points) > 10
            pulses = [o for o in scene.objects if o.name.startswith(f'WFRL.WakeProxy.{tid}.Pulse')]
            assert len(pulses) == wake.PROXY_PULSE_COUNT
            assert all(not o.visible_shadow for o in pulses + rings + lines)
for enabled in (False, True):
    scene.wfrl_show_wake = enabled
    for obj in scene.objects:
        if obj.name.startswith('WFRL.WakeProxy.'):
            assert obj.hide_render == (obj.name.endswith('.Volume') or not enabled)
assert not scene.wfrl_show_lidar
for obj in scene.objects:
    if obj.name.startswith(('WFRL.Grid.', 'WFRL.Inflow.', 'WFRL.Fixture.T1.')):
        assert obj.hide_render, obj.name
scene.wfrl_show_lidar = True
assert not scene.objects['WFRL.Fixture.T1.SensorFrustum'].hide_render
print('WFRL_WAKE_PLACEMENT=PASS')
