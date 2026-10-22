"""Finite saved-motion blade clearance for the centreline Down installation."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import math
import sys
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import cameras, farm_flex

started = time.monotonic()
assert not hasattr(bpy.types.Scene, 'wfrl_gimbal_kind'), 'Run with --factory-startup'
addon.register()
scene = bpy.context.scene
scene['wfrl_blade_model'] = 'rootkink'
addon.load_demo_scene(farm_flex.default_package(), camera_rig=False)
active = farm_flex.active_for(scene)
assert active.model == 'rootkink'
active.visible_turbines = set(range(len(active.readers)))
active.cache.clear()

mounts = {}
for tid in active.readers:
    camera = cameras.ensure_gimbal(scene, tid)
    cameras.down_gimbal(camera)
    anchor = Vector(camera['wfrl_mount_anchor'])
    centre = camera.location.copy()
    assert (centre - anchor - Vector((0, 0, -.4))).length < 1e-6
    mounts[tid] = {'anchor_root_m': list(anchor), 'optical_centre_root_m': list(centre),
                   'extension_root_m': list(centre-anchor)}

sample_indices = np.unique(np.rint(np.linspace(0, len(active.times)-1, 120)).astype(int))
segment_nodes = 81
rays = [Vector(v).normalized() for v in ((1, .371, .127), (-.219, 1, .443), (.137, -.287, 1))]


def parity_inside(bvh, point):
    votes = []
    for direction in rays:
        origin = point.copy()
        count = 0
        for _ in range(100):
            hit, _normal, _index, _distance = bvh.ray_cast(origin, direction, 200.)
            if hit is None:
                break
            count += 1
            origin = hit + direction*1e-5
        else:
            raise AssertionError('Ray parity failed to terminate')
        votes.append(bool(count % 2))
    assert len(set(votes)) == 1, ('Ambiguous point containment', list(point), votes)
    return votes[0]


def blade_bvh(obj, root_inverse, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        transform = root_inverse @ evaluated.matrix_world
        vertices = [transform @ vertex.co for vertex in mesh.vertices]
        faces = [tuple(polygon.vertices) for polygon in mesh.polygons]
        edges = Counter(tuple(sorted((a, b))) for face in faces
                        for a, b in zip(face, face[1:]+face[:1]))
        topology = {'vertices': len(vertices), 'faces': len(faces),
                    'boundary_edges': sum(n == 1 for n in edges.values()),
                    'nonmanifold_edges': sum(n > 2 for n in edges.values()),
                    'loose_vertices': len(vertices)-len({i for face in faces for i in face})}
        return BVHTree.FromPolygons(vertices, faces), topology
    finally:
        evaluated.to_mesh_clear()


rows = []
topologies = {}
for number, source_index in enumerate(sample_indices):
    sim_time = float(active.times[source_index])
    active.update(scene, sim_time_s=sim_time, record_telemetry=False, update_comparison=False)
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for tid in active.readers:
        root = scene.objects[f'WFRL.Turbine.{tid}.YawRoot']
        root_inverse = root.matrix_world.inverted()
        anchor = Vector(mounts[tid]['anchor_root_m'])
        centre = Vector(mounts[tid]['optical_centre_root_m'])
        delta = centre-anchor
        segment = [anchor+delta*(j/(segment_nodes-1)) for j in range(segment_nodes)]
        distances = []
        crossings = []
        containment = []
        per_blade = {}
        for blade in (1, 2, 3):
            obj = scene.objects[f'WFRL.Turbine.{tid}.Blade{blade}']
            bvh, topology = blade_bvh(obj, root_inverse, depsgraph)
            if obj.name in topologies:
                assert topologies[obj.name] == topology
            topologies[obj.name] = topology
            node_distances = [float(bvh.find_nearest(point)[3]) for point in segment]
            minimum = min(node_distances)
            hit, _normal, _face, hit_distance = bvh.ray_cast(anchor, delta.normalized(), delta.length)
            if hit is not None:
                crossings.append({'blade': blade, 'distance_from_anchor_m': float(hit_distance),
                                  'position_root_m': list(hit)})
            closed = topology['boundary_edges'] == 0 and topology['nonmanifold_edges'] == 0
            inside = {name: parity_inside(bvh, point) for name, point in
                      (('anchor', anchor), ('optical_centre', centre))} if closed else None
            if inside and any(inside.values()):
                containment.append({'blade': blade, **inside})
            per_blade[str(blade)] = {
                'optical_centre_surface_distance_m': node_distances[-1],
                'segment_sampled_min_surface_distance_m': minimum,
                'segment_surface_distance_lower_bound_m': max(0., minimum-delta.length/(2*(segment_nodes-1))),
                'closed_surface_endpoint_inside': inside,
            }
            distances.append((minimum, blade, node_distances[-1]))
        closest_segment = min(distances)
        closest_centre = min(distances, key=lambda value: value[2])
        rows.append({'source_index': int(source_index), 'time_s': sim_time, 'turbine': tid,
                     'optical_centre_surface_distance_m': closest_centre[2],
                     'optical_centre_nearest_blade': closest_centre[1],
                     'segment_sampled_min_surface_distance_m': closest_segment[0],
                     'segment_surface_distance_lower_bound_m': max(0., closest_segment[0]-delta.length/(2*(segment_nodes-1))),
                     'segment_nearest_blade': closest_segment[1],
                     'segment_surface_crossings': crossings, 'closed_surface_endpoint_containment': containment,
                     'blades': per_blade})
    if number % 20 == 0 or number == len(sample_indices)-1:
        print('MOUNT_CLEARANCE_PROGRESS', number+1, len(sample_indices), round(time.monotonic()-started, 2), flush=True)

closed_all = all(t['boundary_edges'] == 0 and t['nonmanifold_edges'] == 0 for t in topologies.values())
intersections = [row for row in rows if row['segment_surface_crossings'] or row['closed_surface_endpoint_containment']]
summary = {}
for tid in active.readers:
    selected = [row for row in rows if row['turbine'] == tid]
    centre_row = min(selected, key=lambda row: row['optical_centre_surface_distance_m'])
    segment_row = min(selected, key=lambda row: row['segment_surface_distance_lower_bound_m'])
    summary[tid] = {'samples': len(selected), 'sampled_intersections': sum(bool(row['segment_surface_crossings'] or row['closed_surface_endpoint_containment']) for row in selected),
                    'minimum_optical_centre_surface_distance_m': centre_row['optical_centre_surface_distance_m'],
                    'minimum_optical_centre_at_time_s': centre_row['time_s'],
                    'minimum_optical_centre_nearest_blade': centre_row['optical_centre_nearest_blade'],
                    'minimum_segment_surface_distance_lower_bound_m': segment_row['segment_surface_distance_lower_bound_m'],
                    'minimum_segment_at_time_s': segment_row['time_s'],
                    'minimum_segment_nearest_blade': segment_row['segment_nearest_blade']}
package = Path(farm_flex.default_package())
result = {
    'status': 'PASS_FINITE_SAMPLES' if not intersections and closed_all else 'REVIEW_REQUIRED',
    'module_file': addon.__file__, 'blender': bpy.app.version_string,
    'blade_model': active.model, 'source_package': str(package),
    'source_manifest_sha256': hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),
    'rootkink_asset_sha256': hashlib.sha256((Path(addon.__file__).parent/'assets/rootkink_geometry.json').read_bytes()).hexdigest(),
    'source_time_range_s': [float(active.times[0]), float(active.times[-1])],
    'source_samples': len(active.times), 'uniform_saved_sample_indices': sample_indices.tolist(),
    'sample_count_per_turbine': len(sample_indices), 'total_turbine_samples': len(rows),
    'mounts': mounts, 'blade_mesh_topology': topologies, 'all_surfaces_closed_manifold': closed_all,
    'method': {'geometry': 'Evaluated flexible rootkink blade meshes in the contemporaneous YawRoot frame',
               'point_containment': 'Three non-axis-aligned ray parity votes per endpoint for closed manifold surfaces',
               'segment_intersection': 'BVH ray intersection on the anchor-to-optical-centre segment, plus endpoint containment',
               'segment_distance_bound': '81 equally spaced nodes; minimum node distance minus half node spacing, using the 1-Lipschitz surface distance bound',
               'segment_node_count': segment_nodes},
    'scope': 'Finite saved-motion samples of nine blade meshes, ideal optical point and zero-radius installation segment; not continuous swept-volume clearance, camera/bracket volume clearance, physical certification or an installation safety limit.',
    'summary': summary, 'intersections': intersections, 'samples': rows,
    'elapsed_s': time.monotonic()-started,
}
(OUT/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
print('MOUNT_CLEARANCE_RESULT', json.dumps({key: result[key] for key in ('status', 'summary', 'all_surfaces_closed_manifold', 'elapsed_s')}), flush=True)
assert not intersections, intersections
assert closed_all, topologies
