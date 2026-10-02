"""Audit a fixed emitter's finite envelope against every saved blade pose.

This is a saved-grid geometric audit, not a continuous-motion or real-hardware
installation certificate. No FAST.Farm run or geometry interpolation is used.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.camera_video.data import SourceGeometry
from wfrl.lidar.dual_beam import validate_calibration
from wfrl.lidar.dual_beam_replay import digest


def closest_points_on_triangles(point, triangles):
    """Exact Euclidean closest points, including degenerate edges/triangles."""
    point = np.asarray(point, float)
    triangles = np.asarray(triangles, float)
    if (point.shape != (3,) or triangles.ndim != 3 or triangles.shape[1:] != (3, 3)
            or not len(triangles) or not np.isfinite(point).all()
            or not np.isfinite(triangles).all()):
        raise ValueError('Expected a finite point and nonempty (n, 3, 3) triangles')
    starts = triangles
    edges = np.roll(triangles, -1, axis=1) - starts
    length2 = np.einsum('nij,nij->ni', edges, edges)
    fraction = np.divide(np.einsum('nij,nij->ni', point - starts, edges), length2,
                         out=np.zeros_like(length2), where=length2 > 0)
    on_edges = starts + np.clip(fraction, 0, 1)[..., None] * edges
    edge_distance2 = np.sum((on_edges - point) ** 2, axis=2)
    nearest = on_edges[np.arange(len(triangles)), np.argmin(edge_distance2, axis=1)]

    a = triangles[:, 0]
    ab, ac = triangles[:, 1] - a, triangles[:, 2] - a
    normal = np.cross(ab, ac)
    normal2 = np.einsum('ij,ij->i', normal, normal)
    # A zero normal is handled entirely by the edge calculation above.
    scale = np.divide(np.einsum('ij,ij->i', point - a, normal), normal2,
                      out=np.zeros_like(normal2), where=normal2 > 0)
    projection = point - scale[:, None] * normal
    ap = projection - a
    u = np.divide(np.einsum('ij,ij->i', np.cross(ap, ac), normal), normal2,
                  out=np.zeros_like(normal2), where=normal2 > 0)
    v = np.divide(np.einsum('ij,ij->i', np.cross(ab, ap), normal), normal2,
                  out=np.zeros_like(normal2), where=normal2 > 0)
    inside = (normal2 > 0) & (u >= 0) & (v >= 0) & (u + v <= 1)
    nearest[inside] = projection[inside]
    return nearest


def nearest_surface(point, vertices, indices):
    """Exact nearest triangle, pruning only by a conservative AABB bound.

    The closest referenced vertex supplies an achievable upper distance bound.
    Every triangle whose AABB can improve that bound is evaluated exactly.
    """
    point = np.asarray(point, float)
    vertices = np.asarray(vertices, float)
    indices = np.asarray(indices)
    if (point.shape != (3,) or vertices.ndim != 2 or vertices.shape[1] != 3
            or indices.ndim != 2 or indices.shape[1] != 3 or not len(indices)
            or not np.issubdtype(indices.dtype, np.integer)
            or np.any(indices < 0) or np.any(indices >= len(vertices))
            or not np.isfinite(point).all() or not np.isfinite(vertices).all()):
        raise ValueError('Invalid point or triangle mesh')
    faces = vertices[indices]
    return _nearest_prepared(point, faces, faces.min(axis=1), faces.max(axis=1))


def _nearest_prepared(point, faces, lower, upper):
    # Any referenced vertex is a valid upper bound; one per face is sufficient.
    bound2 = float(np.min(np.sum((faces[:, 0] - point) ** 2, axis=1)))
    offset = np.maximum(0, np.maximum(lower - point, point - upper))
    lower2 = np.einsum('ij,ij->i', offset, offset)
    candidates = np.flatnonzero(lower2 <= bound2 + 1e-12 * max(1., bound2))
    nearest = closest_points_on_triangles(point, faces[candidates])
    distance2 = np.sum((nearest - point) ** 2, axis=1)
    pick = int(np.argmin(distance2))
    return float(np.sqrt(distance2[pick])), nearest[pick], int(candidates[pick])


def exterior_triangle_indices(indices, vertices_per_station, station_count=19):
    """Omit internal station caps of the saved, closed, section-by-section loft.

    Distance still uses all source triangles. Containment uses only the exterior:
    side triangles and caps at the first and last span stations. Interior caps
    would otherwise add spurious parity crossings through the solid blade.
    """
    stations = np.asarray(indices) // vertices_per_station
    cap = np.all(stations == stations[:, :1], axis=1)
    return np.flatnonzero(~cap | (stations[:, 0] == 0) | (stations[:, 0] == station_count - 1))


def containment(point, vertices, indices):
    """Odd/even ray classification of a closed exterior using three directions.

    Coincident triangle hits are one crossing. Disagreement is reported as
    unresolved and cannot pass the physical-envelope separation check.
    """
    point, vertices = np.asarray(point, float), np.asarray(vertices, float)
    if np.any(point < vertices.min(axis=0)) or np.any(point > vertices.max(axis=0)):
        return 'outside'
    faces = vertices[indices]
    e1, e2 = faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0]
    offset = point - faces[:, 0]
    q = np.cross(offset, e1)
    votes = []
    for direction in ((1., .371, .173), (.219, 1., .413), (.317, .127, 1.)):
        direction = np.asarray(direction)
        direction /= np.linalg.norm(direction)
        p = np.cross(direction, e2)
        det = np.einsum('ij,ij->i', e1, p)
        inv = np.divide(1., det, out=np.zeros_like(det), where=np.abs(det) > 1e-12)
        u = np.einsum('ij,ij->i', offset, p) * inv
        v = (q @ direction) * inv
        distance = np.einsum('ij,ij->i', e2, q) * inv
        hit = ((np.abs(det) > 1e-12) & (u >= -1e-10) & (v >= -1e-10)
               & (u + v <= 1 + 1e-10) & (distance > 1e-9))
        hits = np.sort(distance[hit])
        crossings = (0 if not len(hits) else
                     1 + int(np.count_nonzero(np.diff(hits) > 1e-8)))
        votes.append(crossings % 2)
    if min(votes) != max(votes):
        return 'unresolved'
    return 'inside' if votes[0] else 'outside'


def mesh_envelope_clearance(point, vertices, indices, exterior_indices, radius, *, prepared=None):
    distance, nearest, triangle = (nearest_surface(point, vertices, indices) if prepared is None
                                   else _nearest_prepared(point, *prepared))
    state = ('on_surface' if distance <= 1e-9 else
             containment(point, vertices, indices[exterior_indices]))
    signed = -distance if state == 'inside' else distance
    gap = None if state == 'unresolved' else signed - radius
    return dict(center_surface_distance_m=distance, center_containment=state,
                hardware_envelope_gap_m=gap, nearest_surface_world_m=nearest.tolist(),
                source_triangle_index=triangle)


def rendered_installation_reference(asset):
    data = json.loads(Path(asset).read_text())
    s, shell = data['scalars'], data['shell']
    center_x = shell['nacelle_x_bias'] * s['OverHang']
    center_z = s['TowerHt'] + s['Twr2Shft'] + .15
    return dict(source=str(Path(asset).resolve()), source_sha256=digest(asset),
                scope='current rendered envelope only; not hardware or structural certification',
                frame='rest nacelle material frame, including static tower-top height',
                shell_dimensions_m=[shell['length'], shell['width'], shell['height']],
                shell_center_m=[center_x, 0., center_z],
                shell_x_extent_m=[center_x - shell['length']/2, center_x + shell['length']/2],
                shell_y_extent_m=[-shell['width']/2, shell['width']/2],
                shell_central_underside_z_m=center_z - shell['height']/2,
                shell_front_end_underside_z_m=center_z - .72*shell['height']/2,
                tower_top_z_m=s['TowerHt'], yaw_pedestal_radius_m=1.90,
                yaw_bearing_radius_m=1.98,
                hub_m=[s['OverHang']*math.cos(math.radians(s['ShftTilt'])), 0.,
                       s['TowerHt']+s['Twr2Shft']+s['OverHang']*math.sin(math.radians(s['ShftTilt']))],
                formulas_source='blender_frontend/wfrl_blender/scene_builder.py: shell translation and Twr2Shft lift')


def rendered_housing_profile():
    """Conservative spheres for the current local radar fitting, not its carrier."""
    return [dict(component='body', offset_rest_m=[0., 0., .125], radius_m=.19),
            dict(component='bracket', offset_rest_m=[0., 0., .37], radius_m=.14),
            dict(component='plate_pad', offset_rest_m=[0., 0., .50], radius_m=.16),
            dict(component='cable', offset_rest_m=[-.145, 0., .35], radius_m=.20)]


def audit_source(source, origins, radius, progress=True):
    # Shared optical origin means one sphere, with all corresponding beam labels.
    unique, inverse = np.unique(origins, axis=0, return_inverse=True)
    groups = [[f'S{i+1}' for i in np.flatnonzero(inverse == j)] for j in range(len(unique))]
    locations = [dict(beam_ids=g, origin_rest_m=o.tolist(), optical_origin_rest_m=o.tolist(),
                      component='emitter_sphere', envelope_radius_m=radius)
                 for g, o in zip(groups, unique)]
    return _audit_locations(source, locations, progress)


def audit_rendered_source(source, origins, progress=True):
    unique, inverse = np.unique(origins, axis=0, return_inverse=True)
    locations = []
    for j, origin in enumerate(unique):
        groups = [f'S{i+1}' for i in np.flatnonzero(inverse == j)]
        for part in rendered_housing_profile():
            locations.append(dict(beam_ids=groups,
                                  origin_rest_m=(origin + part['offset_rest_m']).tolist(),
                                  optical_origin_rest_m=origin.tolist(),
                                  component=part['component'], offset_rest_m=part['offset_rest_m'],
                                  envelope_radius_m=part['radius_m']))
    return _audit_locations(source, locations, progress)


def _audit_locations(source, locations, progress):
    ext = exterior_triangle_indices(source.blade_triangles, source.blade_reference.shape[2],
                                    source.blade_reference.shape[1])
    summaries = [dict(**location, poses_audited=0,
                      blade_pose_checks=0, nonpositive_gap_checks=0,
                      containment_unresolved_checks=0, inside_center_checks=0,
                      minimum=None, by_blade={str(b): None for b in (1, 2, 3)})
                 for location in locations]
    for index, time in enumerate(source.times):
        tr = source.transforms[index]
        blades = (np.einsum('bsij,bsvj->bsvi', tr[..., :3], source.blade_reference)
                  + tr[:, :, None, :, 3]).reshape(3, -1, 3) + source.layout
        nacelle = source.nacelles[index]
        prepared = []
        for vertices in blades:
            faces = vertices[source.blade_triangles]
            prepared.append((faces, faces.min(axis=1), faces.max(axis=1)))
        for summary in summaries:
            origin = np.asarray(summary['origin_rest_m'])
            radius = summary['envelope_radius_m']
            world = nacelle[:, :3] @ origin + nacelle[:, 3] + source.layout
            summary['poses_audited'] += 1
            for b, vertices in enumerate(blades, 1):
                check = mesh_envelope_clearance(world, vertices, source.blade_triangles, ext, radius,
                                               prepared=prepared[b-1])
                check.update(time_s=float(time), source_index=index, blade_id=b,
                             turbine_id=source.turbine_id, emitter_world_m=world.tolist(),
                             beam_ids=summary['beam_ids'], origin_rest_m=origin.tolist(),
                             component=summary['component'],
                             optical_origin_rest_m=summary['optical_origin_rest_m'],
                             pose=dict(zip(('yaw_deg', 'azimuth_deg', 'rotor_speed_rpm',
                                            'pitch1_deg', 'pitch2_deg', 'pitch3_deg'),
                                           map(float, source.poses[index]))))
                summary['blade_pose_checks'] += 1
                gap = check['hardware_envelope_gap_m']
                summary['containment_unresolved_checks'] += gap is None
                summary['inside_center_checks'] += check['center_containment'] == 'inside'
                summary['nonpositive_gap_checks'] += gap is not None and gap <= 0
                if gap is not None:
                    for container, key in ((summary, 'minimum'), (summary['by_blade'], str(b))):
                        previous = container[key]
                        if previous is None or gap < previous['hardware_envelope_gap_m']:
                            container[key] = check
        if progress and (index % 400 == 0 or index + 1 == len(source.times)):
            print(source.turbine_id, index + 1, '/', len(source.times), flush=True)
    for summary in summaries:
        summary['saved_pose_envelope_separation'] = (
            'intersects_or_inside' if summary['nonpositive_gap_checks'] else
            'unresolved' if summary['containment_unresolved_checks'] else 'separated')
    return dict(turbine_id=source.turbine_id, source_samples=len(source.times),
                source_triangle_count=len(source.blade_triangles), exterior_triangle_count=len(ext),
                mounts=summaries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hardware-radius-m', type=float, default=.60,
                        help='Spherical fitting envelope about each emitter (default: 0.60 m).')
    parser.add_argument('--housing-profile', choices=('sphere', 'rendered'), default='sphere',
                        help='Use one emitter sphere or four current rendered-fitting component spheres.')
    args = parser.parse_args()
    if not math.isfinite(args.hardware_radius_m) or args.hardware_radius_m <= 0:
        parser.error('--hardware-radius-m must be finite and positive')
    if args.output.exists():
        parser.error('--output must be a new file; existing evidence is preserved')
    config = json.loads(args.calibration.read_text())
    origins, _, _ = validate_calibration(config)
    manifest = json.loads((args.source/'manifest.json').read_text())
    results = []
    for tid in manifest['turbine_ids']:
        source = SourceGeometry(args.source, tid)
        results.append(audit_rendered_source(source, origins) if args.housing_profile == 'rendered'
                       else audit_source(source, origins, args.hardware_radius_m))
    witnesses = [m['minimum'] for t in results for m in t['mounts'] if m['minimum'] is not None]
    states = [m['saved_pose_envelope_separation'] for t in results for m in t['mounts']]
    overall = ('intersects_or_inside' if 'intersects_or_inside' in states else
               'unresolved' if 'unresolved' in states else 'separated')
    result = dict(schema='wfrl.dual-beam-mount-audit.v1', status='REVIEW_ONLY',
                  source_package=str(args.source.resolve()), source_hashes=source.source_hashes,
                  calibration_path=str(args.calibration.resolve()), calibration_sha256=digest(args.calibration),
                  calibration_id=config['id'], housing_profile=args.housing_profile,
                  hardware_envelope_radius_m=args.hardware_radius_m if args.housing_profile == 'sphere' else None,
                  rendered_housing_parts=rendered_housing_profile() if args.housing_profile == 'rendered' else None,
                  envelope_basis=('four conservative component spheres around current rendered local fitting; carrier excluded'
                                  if args.housing_profile == 'rendered' else
                                  'conservative sphere about optical emitter; default 0.60 m covers current local rendered radar fitting up to z+0.53 m; lateral support boom and real hardware unspecified'),
                  sample_kind='source', source_fps=manifest['source_fps'], segment=manifest['segment'],
                  saved_pose_envelope_separation=overall,
                  minimum=min(witnesses, key=lambda x: x['hardware_envelope_gap_m']) if witnesses else None,
                  turbines=results,
                  rendered_installation=rendered_installation_reference(
                      ROOT/'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json'),
                  method='exact point-to-triangle minimum with conservative AABB pruning; sphere radius subtracted from signed center distance',
                  containment_method='three-ray parity against exterior loft; interior span caps omitted; disagreement unresolved',
                  limitations=['Saved poses only; no bound on inter-sample blade motion.',
                               'All saved blade surfaces checked; nacelle, tower, spinner and the additional support carrier are excluded. The rendered profile includes the local radar bracket.',
                               'Rendered shell reference is descriptive; no real installation or load-bearing certification.',
                               'Mount selection and clearance do not establish laser hit coverage or reconstruction accuracy.'],
                  implementation_sha256=digest(Path(__file__)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects against another process writing this path.
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
    print(args.output.resolve(), flush=True)


if __name__ == '__main__':
    main()
