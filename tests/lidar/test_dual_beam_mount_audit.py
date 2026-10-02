"""Independent analytic cases for finite-mount clearance, not vertex distance."""
from types import SimpleNamespace
import ast
from itertools import product
from pathlib import Path

import numpy as np
import pytest

from scripts.lidar.audit_dual_beam_mount import (
    audit_rendered_source, audit_source, closest_points_on_triangles, containment,
    exterior_triangle_indices, mesh_envelope_clearance, nearest_surface,
    rendered_housing_profile,
)


def tetrahedron():
    vertices = np.array([[0., 0., 0.], [2., 0., 0.], [0., 2., 0.], [0., 0., 2.]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
    return vertices, faces


def test_triangle_interior_edge_and_degenerate_point():
    triangle = np.array([[[0., 0., 0.], [4., 0., 0.], [0., 4., 0.]]])
    assert closest_points_on_triangles([1., 1., 3.], triangle)[0] == pytest.approx([1., 1., 0.])
    assert closest_points_on_triangles([3., 3., 1.], triangle)[0] == pytest.approx([2., 2., 0.])
    degenerate = np.array([[[0., 0., 0.], [2., 0., 0.], [1., 0., 0.]],
                           [[2., 3., 4.], [2., 3., 4.], [2., 3., 4.]]])
    assert closest_points_on_triangles([1., 2., 0.], degenerate) == pytest.approx(
        np.array([[1., 0., 0.], [2., 3., 4.]]))


def test_conservative_pruning_does_not_drop_large_triangle_interior():
    # All vertices of the winning triangle are far away, but its interior is near.
    vertices = np.array([[-100., -100., 0.], [100., -100., 0.], [0., 100., 0.],
                         [0., 0., 3.], [1., 0., 3.], [0., 1., 3.]])
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    distance, nearest, face = nearest_surface([0., 0., .25], vertices, faces)
    assert distance == pytest.approx(.25)
    assert nearest == pytest.approx([0., 0., 0.])
    assert face == 0


def test_aabb_pruning_matches_all_triangle_calculation():
    rng = np.random.default_rng(44)
    vertices = rng.normal(size=(90, 3))
    faces = np.arange(90).reshape(-1, 3)
    for point in rng.normal(size=(12, 3)):
        distance, nearest, _ = nearest_surface(point, vertices, faces)
        full = closest_points_on_triangles(point, vertices[faces])
        assert distance == pytest.approx(np.linalg.norm(full - point, axis=1).min(), abs=1e-12)
        assert np.linalg.norm(nearest - point) == pytest.approx(distance)


def test_inside_center_cannot_pass_spherical_hardware_clearance():
    vertices, faces = tetrahedron()
    exterior = np.arange(len(faces))
    assert containment([.3, .3, .3], vertices, faces) == 'inside'
    assert containment([1., 1., 1.], vertices, faces) == 'outside'
    inside = mesh_envelope_clearance([.3, .3, .3], vertices, faces, exterior, .2)
    assert inside['hardware_envelope_gap_m'] == pytest.approx(-.5)
    assert inside['center_containment'] == 'inside'
    outside = mesh_envelope_clearance([.3, .3, -.3], vertices, faces, exterior, .2)
    assert outside['hardware_envelope_gap_m'] == pytest.approx(.1)
    crossing = mesh_envelope_clearance([.3, .3, -.1], vertices, faces, exterior, .2)
    assert crossing['hardware_envelope_gap_m'] == pytest.approx(-.1)


def test_rigid_transform_preserves_surface_distance_and_containment():
    vertices, faces = tetrahedron()
    theta = .72
    rotation = np.array([[np.cos(theta), -np.sin(theta), 0.],
                         [np.sin(theta), np.cos(theta), 0.], [0., 0., 1.]])
    shift = np.array([630., -252., 89.])
    point = np.array([.3, .3, .3])
    transformed = vertices @ rotation.T + shift
    query = rotation @ point + shift
    assert nearest_surface(query, transformed, faces)[0] == pytest.approx(.3, abs=1e-12)
    assert containment(query, transformed, faces) == 'inside'


def test_internal_station_caps_removed_only_for_containment():
    # Three stations with three vertices each: side triangles + endpoint/interior caps.
    faces = np.array([[0, 1, 4], [0, 4, 3], [0, 1, 2], [3, 4, 5], [6, 7, 8]])
    assert exterior_triangle_indices(faces, 3, 3).tolist() == [0, 1, 2, 4]


def test_full_audit_visits_all_poses_blades_and_shared_origin_once():
    cube = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.],
                     [0., 0., 1.], [1., 0., 1.], [1., 1., 1.], [0., 1., 1.]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
                      [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
                      [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0]])
    transforms = np.zeros((2, 3, 2, 3, 4))
    transforms[..., :3] = np.eye(3)
    transforms[:, 1:, :, 0, 3] = np.array([10., 20.])[None, :, None]
    transforms[1, 0, :, 2, 3] = -.75
    nacelles = np.zeros((2, 3, 4))
    nacelles[..., :3] = np.eye(3)
    source = SimpleNamespace(times=np.array([1., 1.025]), transforms=transforms,
                             blade_reference=np.tile(cube.reshape(1, 2, 4, 3), (3, 1, 1, 1)),
                             blade_triangles=faces, nacelles=nacelles, poses=np.zeros((2, 6)),
                             layout=np.zeros(3), turbine_id='T1')
    result = audit_source(source, np.tile([.5, .5, -1.], (3, 1)), .2, progress=False)
    assert len(result['mounts']) == 1
    mount = result['mounts'][0]
    assert mount['beam_ids'] == ['S1', 'S2', 'S3']
    assert mount['poses_audited'] == 2
    assert mount['blade_pose_checks'] == 6
    assert mount['minimum']['hardware_envelope_gap_m'] == pytest.approx(.05)
    assert mount['minimum']['time_s'] == 1.025
    assert mount['minimum']['source_index'] == 1
    assert mount['minimum']['blade_id'] == 1
    assert mount['saved_pose_envelope_separation'] == 'separated'
    assert all(mount['by_blade'][str(b)] is not None for b in (1, 2, 3))
    rendered = audit_rendered_source(source, np.tile([.5, .5, -1.], (3, 1)), progress=False)
    assert {m['component'] for m in rendered['mounts']} == {'body', 'bracket', 'plate_pad', 'cable'}
    assert all(m['beam_ids'] == ['S1', 'S2', 'S3'] for m in rendered['mounts'])
    assert all(m['poses_audited'] == 2 and m['blade_pose_checks'] == 6 for m in rendered['mounts'])
    assert all(m['minimum']['optical_origin_rest_m'] == [.5, .5, -1.] for m in rendered['mounts'])


def test_rendered_spheres_cover_current_primitive_extents_and_cable_controls():
    profile = {p['component']: p for p in rendered_housing_profile()}
    source = Path(__file__).resolve().parents[2] / 'blender_frontend/wfrl_blender/clearance_visual.py'
    tree = ast.parse(source.read_text())
    checked = set()
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == 'part' and len(call.args) >= 3
                and isinstance(call.args[0], ast.Constant)):
            continue
        suffix, center, half = [ast.literal_eval(a) for a in call.args[:3]]
        component = ('bracket' if suffix == '.Bracket' else
                     'plate_pad' if suffix in ('.MountPlate', '.MountPad') else 'body')
        # The gland rotates; enclosing its primitive in a radius max(half) sphere
        # before translation conservatively handles all cylinder orientations.
        if suffix == '.CableGland':
            extent = np.linalg.norm(np.asarray(center) - profile[component]['offset_rest_m']) + max(half)
            assert extent < profile[component]['radius_m']
        else:
            corners = np.asarray(center) + np.array(list(product((-1, 1), repeat=3))) * half
            distance = np.linalg.norm(corners - profile[component]['offset_rest_m'], axis=1)
            assert distance.max() < profile[component]['radius_m']
        checked.add(suffix)
    assert {'', '.Bracket', '.MountPlate', '.MountPad', '.Window', '.ServiceCover', '.CableGland'} <= checked
    for x, z in ((-.061, .044), (.061, .044), (-.061, .206), (.061, .206)):
        assert np.linalg.norm(np.array([x, -.090, z]) - profile['body']['offset_rest_m']) + .01 < .19
    controls = np.array([[-.123, 0., .205], [-.17, 0., .235], [-.16, 0., .35],
                         [-.13, 0., .45], [-.13, 0., .53]])
    # Actual Blender AUTO-handle curve mesh is checked by the companion native
    # evidence script; this unit check covers the declared cable control points.
    assert np.max(np.linalg.norm(controls - profile['cable']['offset_rest_m'], axis=1)) + .006 < .20


def test_invalid_inputs_rejected():
    vertices, faces = tetrahedron()
    with pytest.raises(ValueError):
        nearest_surface([np.nan, 0, 0], vertices, faces)
    with pytest.raises(ValueError):
        nearest_surface([0, 0, 0], vertices, np.array([[0, 1, 50]]))
