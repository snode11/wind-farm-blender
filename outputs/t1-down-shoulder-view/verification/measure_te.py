"""Measure sampled shoulder TE silhouette and first-hit visibility in Blender."""
import argparse
from collections import Counter
import importlib
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


parser = argparse.ArgumentParser()
parser.add_argument('--candidates', type=Path)
parser.add_argument('--output', type=Path, default=Path(__file__).with_name('te_metrics.json'))
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
module = 'bl_ext.user_default.wfrl_blender'
addon = importlib.import_module(module)
package = Path(addon.__file__).resolve().parent
expected = Path('BLENDER_USER_EXTENSIONS/user_default/wfrl_blender')
assert package == expected.resolve(), package
assert module in bpy.context.preferences.addons
cameras = importlib.import_module(module + '.cameras')
farm_flex = importlib.import_module(module + '.farm_flex')
addon.load_demo_scene()
scene = bpy.context.scene
bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='DOWN')
camera = scene.camera
blade = scene.objects['WFRL.Turbine.T1.Blade1']
root = scene.objects['WFRL.Turbine.T1.YawRoot']
active = farm_flex.active_for(scene)
rest = next(row[1] for row in active.blades if row[0] == blade)
hub_radius = float(importlib.import_module(module + '.turbine_geometry').geometry_data()['scalars']['HubRad'])
ring_points = int(np.count_nonzero(np.isclose(rest[:, 2], rest[0, 2], atol=1e-7)))
assert ring_points == 48, ring_points
spans = rest[::ring_points, 2] - hub_radius
segments = [ring for ring in range(len(spans) - 1)
            if spans[ring] >= 10.25 - 1e-5 and spans[ring + 1] <= 18.45 + 1e-5]
assert len(segments) > 2
angles = np.degrees(np.unwrap(np.radians(active.poses[:, 0, 1])))
target = 180 + 360 * math.ceil((angles[0] - 180) / 360)
times = np.interp(target + np.array([-9., 0., 9.]), angles, active.times)
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
camera.data.shift_x = camera.data.shift_y = 0
camera.data.clip_start, camera.data.clip_end = .05, 30000
nominal = dict(name='centered', location=list(camera.location),
               yaw=float(camera['gimbal_yaw']), pitch=float(camera['gimbal_pitch']),
               hfov=float(camera['gimbal_fov']), roll=float(camera['gimbal_roll']))
candidates = [nominal] + [dict(name=f'side_{abs(y):g}', location=[-2.5, y, z],
                              yaw=145, pitch=-80, hfov=85, roll=180)
                         for y, z in ((-1.8, .9), (-2.4, .6), (-3.2, .6), (-4., .6))]
if args.candidates:
    value = json.loads(args.candidates.read_text())
    candidates = value.get('candidates', value) if isinstance(value, dict) else value

overlay_patterns = ('.ClearanceRadar.Beam', '.TipTrail', '.Deflection', '.Reference')
excluded = set()


def scene_triangles(graph):
    vertices, triangles, identities = [], [], []
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.hide_render:
            continue
        if any(pattern in obj.name for pattern in overlay_patterns):
            excluded.add(obj.name)
            continue
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            offset = len(vertices)
            vertices.extend(evaluated.matrix_world @ vertex.co for vertex in mesh.vertices)
            for triangle in mesh.loop_triangles:
                triangles.append(tuple(offset + index for index in triangle.vertices))
                span = None
                if obj == blade:
                    span = tuple(float(rest[index, 2] - hub_radius) for index in triangle.vertices)
                identities.append((obj.name, span))
        finally:
            evaluated.to_mesh_clear()
    points = np.asarray(vertices, dtype=np.float64)[np.asarray(triangles)]
    return (points[:, 0], points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]), identities


def projected(point):
    uv = world_to_camera_view(scene, camera, point)
    inside = camera.data.clip_start < uv.z < camera.data.clip_end and 0 <= uv.x <= 1 and 0 <= uv.y <= 1
    return Vector((uv.x * 1920, uv.y * 1080)), bool(inside)


def first_hit(geometry, identities, eye, point):
    origin = np.asarray(eye, dtype=np.float64)
    direction = np.asarray(point, dtype=np.float64) - origin
    target_distance = np.linalg.norm(direction)
    direction /= target_distance
    vertices, edge1, edge2 = geometry
    cross = np.cross(direction, edge2)
    determinant = np.einsum('ij,ij->i', edge1, cross)
    usable = np.abs(determinant) > 1e-12
    inverse = np.divide(1., determinant, out=np.zeros_like(determinant), where=usable)
    relative = origin - vertices
    u = np.einsum('ij,ij->i', relative, cross) * inverse
    q = np.cross(relative, edge1)
    v = np.einsum('ij,j->i', q, direction) * inverse
    depth = np.einsum('ij,ij->i', edge2, q) * inverse
    tolerance = 1e-7
    hit = usable & (u >= -tolerance) & (v >= -tolerance) & (u + v <= 1 + tolerance)
    hit &= (depth > 1e-8) & (depth <= target_distance + .02)
    indexes = np.flatnonzero(hit)
    if not len(indexes):
        # The target is a known mesh edge. Visibility needs no earlier blocker;
        # an exact boundary ray need not register the zero-width target itself.
        return True, 'CLEAR_RAY'
    index = indexes[np.argmin(depth[indexes])]
    distance = depth[index]
    name, span = identities[index]
    visible = bool(distance >= target_distance - .01)
    label = name
    if span is not None:
        label += f':span{min(span):.2f}-{max(span):.2f}:depth_delta{distance - target_distance:.4f}'
    return visible, label


def chain_metrics(run, rows):
    points = [Vector(rows[run[0]]['points_px'][0])]
    points.extend(Vector(rows[index]['points_px'][1]) for index in run)
    turns = []
    for a, b, c in zip(points, points[1:], points[2:]):
        u, v = b - a, c - b
        if u.length > 1e-6 and v.length > 1e-6:
            turns.append(math.degrees(math.acos(max(-1., min(1., u.normalized().dot(v.normalized()))))))
    axis = points[-1] - points[0]
    bulge = max(abs(axis.x * (p.y - points[0].y) - axis.y * (p.x - points[0].x)) / axis.length
                for p in points) if axis.length > 1e-6 else 0.
    return dict(span_m=[rows[run[0]]['span_m'][0], rows[run[-1]]['span_m'][1]],
                segments=len(run), length_px=round(sum(rows[index]['length_px'] for index in run), 3),
                max_turn_deg=round(max(turns, default=0.), 3),
                max_chord_deviation_px=round(bulge, 3))


results = []
for delta, at in zip((-9., 0., 9.), times):
    frame_value = scene.frame_start + (float(at) - float(active.times[0])) * 60
    scene.frame_set(math.floor(frame_value), subframe=frame_value % 1)
    active.update(scene, sim_time_s=float(at), record_telemetry=False, update_comparison=False)
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    geometry, identities = scene_triangles(graph)
    world = [blade.matrix_world @ vertex.co for vertex in blade.data.vertices]
    blade.data.calc_loop_triangles()
    adjacent = {}
    for ring in segments:
        edge = {ring * ring_points, (ring + 1) * ring_points}
        adjacent[ring] = [triangle for triangle in blade.data.loop_triangles
                          if edge.issubset(triangle.vertices)]
        assert len(adjacent[ring]) == 2
    for candidate in candidates:
        camera.parent = root
        camera.matrix_parent_inverse.identity()
        camera.location = candidate['location']
        camera['gimbal_roll'] = candidate.get('roll', 180)
        camera.data.shift_x = candidate.get('shift_x', 0.)
        camera.data.shift_y = candidate.get('shift_y', 0.)
        cameras.aim_gimbal(camera, candidate['yaw'], candidate['pitch'], candidate['hfov'])
        bpy.context.view_layer.update()
        eye = camera.matrix_world.translation.copy()
        rows, blockers = [], Counter()
        for ring in segments:
            start, end = world[ring * ring_points], world[(ring + 1) * ring_points]
            triangles = [[world[index] for index in triangle.vertices] for triangle in adjacent[ring]]
            centers = [sum(points, Vector()) / 3 for points in triangles]
            normals = [(points[1] - points[0]).cross(points[2] - points[0]).normalized()
                       for points in triangles]
            facing = [normal.dot((eye - center).normalized()) for normal, center in zip(normals, centers)]
            silhouette = min(facing) < 0 < max(facing)
            screen_start, in_start = projected(start)
            screen_end, in_end = projected(end)
            visibility = []
            for fraction in (.1, .5, .9):
                point = (1 - fraction) * np.asarray(start, dtype=np.float64) + fraction * np.asarray(end, dtype=np.float64)
                visible, blocker = first_hit(geometry, identities, eye, point)
                visibility.append(visible)
                if not visible:
                    blockers[blocker] += 1
            rows.append(dict(span_m=[round(float(spans[ring]), 5), round(float(spans[ring + 1]), 5)],
                             silhouette=bool(silhouette), facing=[round(value, 5) for value in facing],
                             visible_samples=sum(visibility), in_frame=bool(in_start and in_end),
                             length_px=round((screen_end - screen_start).length, 3),
                             points_px=[[round(value, 3) for value in point] for point in (screen_start, screen_end)]))
        runs, run = [], []
        for index, row in enumerate(rows):
            accepted = row['silhouette'] and row['visible_samples'] == 3 and row['in_frame']
            if accepted:
                run.append(index)
            elif run:
                runs.append(chain_metrics(run, rows)); run = []
        if run:
            runs.append(chain_metrics(run, rows))
        result = dict(candidate=candidate['name'], delta_from_down_deg=delta, time_s=float(at),
                      frame=scene.frame_current, subframe=scene.frame_subframe,
                      pose=candidate, segment_count=len(rows),
                      silhouette_segments=sum(row['silhouette'] for row in rows),
                      nonoccluded_segments=sum(row['visible_samples'] == 3 for row in rows),
                      in_frame_segments=sum(row['in_frame'] for row in rows),
                      visible_silhouette_segments=sum(item['segments'] for item in runs),
                      longest_chain=max(runs, key=lambda item: item['length_px'], default=None),
                      chains=runs, blockers=dict(blockers), segments=rows)
        results.append(result)
        print('TE_METRIC', candidate['name'], delta, 'visible_silhouette_segments',
              result['visible_silhouette_segments'], 'chain', result['longest_chain'], flush=True)

report = dict(schema='wfrl.te-silhouette-diagnostic.v1', addon_file=addon.__file__,
              build_info=json.loads((package / 'build-info.json').read_text()),
              resolution=[1920, 1080], blade=blade.name, ring_points=ring_points,
              span_origin='blade root; HubRad excluded', target_span_m=[10.25, 18.45],
              samples_per_segment=3, ray_depth_tolerance_m=.01,
              ray_algorithm="float64 Moller-Trumbore", ray_barycentric_tolerance=1e-7,
              edge_rule='opposite adjacent triangle signs, no surface more than 1 cm before any of 3 known edge samples, both endpoints in frame',
              excluded_visual_overlay_objects=sorted(excluded),
              geometry_modified=False, physics_rerun=False,
              scope='finite geometric silhouette samples; no image-recognition or field calibration claim',
              rows=results)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, separators=(',', ':')) + '\n')
print('TE_METRICS_SAVED', str(args.output), flush=True)
