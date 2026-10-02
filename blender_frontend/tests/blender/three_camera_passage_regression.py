"""Sample fixed-camera blade passages over one recorded rotor revolution.

This is geometric visibility evidence, not continuous full-rotation coverage or
an image-quality/physical-hardware acceptance test. Runs in isolated background Blender.
"""
from pathlib import Path
import hashlib
import importlib
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE + '.custom_cameras')
farm = importlib.import_module(MODULE + '.farm_flex')
preview = importlib.import_module(MODULE + '.custom_camera_preview')
rig = importlib.import_module(MODULE + '.stacked_camera_rig')
LAYOUT = Path(os.environ.get('WFRL_CANDIDATE_LAYOUT', '/tmp/wfrl-three-camera-search/candidate-1.json'))
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-three-camera-passage'))
OUT.mkdir(parents=True, exist_ok=True)
INTERVALS = tuple(tuple(v) for v in json.loads(os.environ.get(
    'WFRL_TARGET_INTERVALS', '[[3,11],[23,33],[50,61.5]]')))
TRIANGLE_STRIDE = max(1, int(os.environ.get('WFRL_TRIANGLE_STRIDE', '5')))

addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
# These animated aids can otherwise be re-enabled by frame-change callbacks
# after a one-time hide. They are not physical occluders for this measurement.
scene.wfrl_deflection_visible = False
scene.wfrl_flex_show_tip_trails = False
payload = json.loads(LAYOUT.read_text())
core.import_layout(scene, payload, overwrite=True)
cameras = [core.get_camera(scene, slot) for slot in (1, 2, 3)]
fixed_local = [camera.matrix_basis.copy() for camera in cameras]
active = farm._ACTIVE
times = np.asarray(active.times)
angles = np.degrees(np.unwrap(np.radians(active.poses[:, 0, 1])))
direction = 1. if angles[1] >= angles[0] else -1.
progress = (angles - angles[0]) * direction
assert np.all(np.diff(progress) > 0), 'recorded rotor angle is not monotonic'
assert progress[-1] >= 360, 'source contains less than one rotor revolution'
end_time = float(np.interp(360., progress, times))
timebase = float(scene['wfrl_clearance_timebase_fps'])
frames = sorted(set(int(round(1 + (float(np.interp(a, progress, times)) - times[0]) * timebase))
                    for a in np.linspace(0., 360., 73)))
if os.environ.get('WFRL_REFERENCE_ONLY') == '1':
    frames = [1]

blades = []
for row in active.blades:
    obj, rest = row[:2]
    if not obj.name.startswith('WFRL.Turbine.T1.Blade'):
        continue
    used = np.unique([i for poly in obj.data.polygons for i in poly.vertices])
    blades.append((obj, rest, used, rest[used, 2] - 1.5))

results = []
occluders = {}
with preview.without_annotations(scene, bpy.context.view_layer):
    for frame in frames:
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        deps = bpy.context.evaluated_depsgraph_get()
        for camera, original in zip(cameras, fixed_local):
            assert max(abs(camera.matrix_basis[i][j] - original[i][j])
                       for i in range(4) for j in range(4)) < 1e-7, 'camera tracked a moving blade'
        meshes = []
        for obj, rest, used, vertex_spans in blades:
            evaluated = obj.evaluated_get(deps)
            # Re-triangulate the evaluated deformed mesh: a quad centroid or
            # its first-three-vertex normal need not lie on the actual surface.
            evaluated.data.calc_loop_triangles()
            ids = np.array([list(tri.vertices) for tri in list(evaluated.data.loop_triangles)[::TRIANGLE_STRIDE]], dtype=int)
            spans = rest[ids, 2].mean(axis=1) - 1.5
            coords = np.empty(len(evaluated.data.vertices) * 3, dtype=np.float32)
            evaluated.data.vertices.foreach_get('co', coords)
            coords = coords.reshape(-1, 3)
            matrix = np.asarray(evaluated.matrix_world)
            world = coords @ matrix[:3, :3].T + matrix[:3, 3]
            centers = world[ids].mean(axis=1)
            normals = np.cross(world[ids[:, 1]] - world[ids[:, 0]], world[ids[:, 2]] - world[ids[:, 0]])
            meshes.append((obj, centers, normals, spans, world[used], vertex_spans))
        observations = []
        for slot, camera, interval in zip((1, 2, 3), cameras, INTERVALS):
            params = core.parameters(camera)
            inverse = np.asarray(camera.evaluated_get(deps).matrix_world.inverted())
            origin = np.asarray(camera.evaluated_get(deps).matrix_world.translation)
            housing_front = -np.asarray(scene.objects[rig.PREFIX].matrix_world.to_3x3())[:, 1]
            housing_front /= np.linalg.norm(housing_front)
            half = np.tan(np.radians([params.fov, params.vfov]) / 2)
            per_blade = []
            for obj, centers, normals, spans, vertices, vertex_spans in meshes:
                view = centers @ inverse[:3, :3].T + inverse[:3, 3]
                frontal = np.einsum('ij,ij->i', normals, origin - centers) > 0
                target = (spans >= interval[0]) & (spans <= interval[1]) & frontal
                inside = (view[:, 2] < -params.clip_near_m) & (np.abs(view[:, :2]) <= -view[:, 2, None] * half).all(axis=1)
                vertex_view = vertices @ inverse[:3, :3].T + inverse[:3, 3]
                vertex_inside = ((vertex_view[:, 2] < -params.clip_near_m)
                                 & (np.abs(vertex_view[:, :2]) <= -vertex_view[:, 2, None] * half).all(axis=1))
                visible_spans = []
                visible_bands = set()
                target_blockers = {}
                blocked_direction_cos = []
                # Check every longitudinal band inside this camera, not only
                # its nominal assignment: union and overlaps use actual views.
                candidates = np.flatnonzero(frontal & inside)
                for index in candidates:
                    delta = centers[index] - origin
                    distance = float(np.linalg.norm(delta))
                    hit, location, _, _, hit_obj, _ = scene.ray_cast(
                        deps, Vector(origin), Vector(delta / distance), distance=distance + .15)
                    visible = (hit and hit_obj.original == obj
                               and np.linalg.norm(np.asarray(location) - centers[index]) < .01)
                    if visible:
                        visible_bands.add(round(float(spans[index]), 4))
                        if target[index]:
                            visible_spans.append(float(spans[index]))
                    else:
                        name = hit_obj.original.name if hit and hit_obj else 'none'
                        occluders[name] = occluders.get(name, 0) + 1
                        if target[index]:
                            target_blockers[name] = target_blockers.get(name, 0) + 1
                            blocked_direction_cos.append(float(np.dot(delta / distance, housing_front)))
                span = ([round(min(visible_spans), 3), round(max(visible_spans), 3)] if visible_spans else None)
                # A passage needs multiple visible surface samples over a
                # substantial part of its assigned section, not one edge pixel.
                usable = len(visible_spans) >= 3 and span[1] - span[0] >= .25 * (interval[1] - interval[0]) if span else False
                per_blade.append({'blade': obj.name.rsplit('.', 1)[1], 'front_samples': int(target.sum()),
                                  'in_frame': int((target & inside).sum()), 'visible': len(visible_spans),
                                  'visible_span_m': span, 'section_visible': bool(usable),
                                  'target_blockers': target_blockers,
                                  'blocked_ray_housing_front_cos_range': [min(blocked_direction_cos), max(blocked_direction_cos)] if blocked_direction_cos else None,
                                  'blocked_rays_behind_front_plane': sum(v <= 0 for v in blocked_direction_cos),
                                  '_visible_bands': visible_bands, '_vertex_inside': vertex_inside})
            observations.append(per_blade)
        coverage = []
        for i, (obj, _, _, spans, _, vertex_spans) in enumerate(meshes):
            projected = [observations[j][i].pop('_vertex_inside') for j in range(3)]
            bands = [observations[j][i].pop('_visible_bands') for j in range(3)]
            union = np.logical_or.reduce(projected)
            all_bands = set(round(float(s), 4) for s in spans)
            visible_union = set.union(*bands)
            missing_vertices = vertex_spans[~union]
            overlaps = []
            for j in (0, 1):
                shared = sorted(bands[j] & bands[j + 1])
                overlaps.append({'band_count': len(shared), 'span_m': [shared[0], shared[-1]] if shared else None,
                                 'bands_m': shared})
            coverage.append({'blade': obj.name.rsplit('.', 1)[1],
                             'frustum_union_all_vertices': bool(union.all()),
                             'frustum_union_vertex_fraction': round(float(union.mean()), 6),
                             'uncovered_vertex_span_m': [float(missing_vertices.min()), float(missing_vertices.max())] if len(missing_vertices) else None,
                             'visible_sample_band_fraction': round(len(visible_union) / len(all_bands), 6),
                             'missing_visible_sample_bands_m': sorted(all_bands - visible_union),
                             'adjacent_visible_overlap': overlaps})
        common = [blades[i][0].name.rsplit('.', 1)[1] for i in range(3)
                  if all(observations[slot][i]['section_visible'] for slot in range(3))]
        results.append({'frame': frame, 'time_s': round(float(scene['wfrl_clearance_time_s']), 6),
                        'turn_deg': round(float(np.interp(scene['wfrl_clearance_time_s'], times, progress)), 3),
                        'same_blade_all_sections': common,
                        'all_cameras_have_section': all(any(b['section_visible'] for b in camera) for camera in observations),
                        'same_blade_full_length_geometry': coverage,
                        'C1': observations[0], 'C2': observations[1], 'C3': observations[2]})

passes = [{'frame': row['frame'], 'time_s': row['time_s'], 'blade': row['same_blade_all_sections']}
          for row in results if row['same_blade_all_sections']]
full_length_passes = [{'frame': row['frame'], 'time_s': row['time_s'], 'blade': geometry['blade']}
                     for row in results for geometry in row['same_blade_full_length_geometry']
                     if geometry['frustum_union_all_vertices']
                     and not geometry['missing_visible_sample_bands_m']
                     and all(overlap['band_count'] for overlap in geometry['adjacent_visible_overlap'])]
report = {
    'status': 'GEOMETRY_MEASURED',
    'module': MODULE, 'blender': bpy.app.version_string, 'candidate': str(LAYOUT),
    'candidate_sha256': hashlib.sha256(LAYOUT.read_bytes()).hexdigest(),
    'intervals_m': INTERVALS, 'recorded_revolution_s': [float(times[0]), end_time],
    'sampling': {'requested_angle_step_deg': 5, 'count': len(results), 'timeline_fps': timebase,
                 'sampled_time_s': [results[0]['time_s'], results[-1]['time_s']],
                 'sampled_turn_deg': [results[0]['turn_deg'], results[-1]['turn_deg']],
                 'method': f'nearest timeline frames at 5 degree increments; evaluated loop-triangle centroid every {TRIANGLE_STRIDE} triangles and scene ray casts; target-hit tolerance 0.01 m'},
    'section_visible_criterion': 'at least 3 ray-visible front-face samples spanning at least 25 percent of the assigned section',
    'same_blade_simultaneous_passages': passes,
    'full_length_geometry_with_overlap_samples': full_length_passes,
    'all_cameras_have_section_count': sum(row['all_cameras_have_section'] for row in results),
    'per_camera_section_visible_sample_count': {key: sum(any(b['section_visible'] for b in row[key]) for row in results)
                                               for key in ('C1', 'C2', 'C3')},
    'reference_visibility': {key: results[0][key] for key in ('C1', 'C2', 'C3')},
    'reference_full_length_geometry': results[0]['same_blade_full_length_geometry'],
    'full_length_geometry_criteria': 'Projected union over all used vertices plus ray-visible polygon sample bands; adjacent overlaps list actual shared visible span bands. A min/max range does not prove every point or every surface is visible.',
    'fixed_camera_local_transforms': True, 'occluders': occluders,
    'limits': 'Sampled passage evidence only. Cameras are fixed to the nacelle; blade sections are not always visible. No claim of continuous/full-revolution coverage, complete surface visibility, image quality, or physical hardware validation.',
    'samples': results,
}
(OUT / 'rotation-visibility.json').write_text(json.dumps(report, indent=2))
summary = {k: v for k, v in report.items() if k != 'samples'}
(OUT / 'rotation-summary.json').write_text(json.dumps(summary, indent=2))
print('THREE_CAMERA_PASSAGE', json.dumps({k: v for k, v in summary.items() if k != 'reference_visibility'}), flush=True)
assert passes, 'no simultaneous same-blade section passage in sampled revolution'
