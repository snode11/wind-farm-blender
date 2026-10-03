"""Independent area audit: exact partition, coplanar ownership and index topology."""
import numpy as np
import pytest

from wfrl.nrel_reconstruction.area_audit import audit_metadata, audit_partition, audit_topology, run_audit
from wfrl.nrel_reconstruction.evaluate import Mesh, triangle_areas


def test_crossing_triangle_is_clipped_not_centroid_assigned():
    mesh = Mesh([[0, 0, -1], [2, 0, 1], [0, 0, 1]], [[0, 1, 2]])
    report, rows, pieces = audit_partition(mesh, (0, 3))
    assert report["global_area_m2"] == pytest.approx(2)
    assert report["domains"]["below_span"]["area_m2"] == pytest.approx(.5)
    assert report["domains"]["root"]["area_m2"] == pytest.approx(1.5)
    assert report["closure_residual_m2"] == pytest.approx(0, abs=1e-14)
    assert rows[0]["below_span_area_m2"] == pytest.approx(.5)
    assert pieces[0]["source_triangle_id"] == 0
    assert max(p[2] for p in pieces[0]["clipped_polygon_xyz_m"]) == 0


def test_coplanar_faces_owned_once_including_endpoints():
    vertices = [[x, y, z] for z in (0, 1, 2, 3) for x, y in ((0, 0), (1, 0), (0, 1))]
    mesh = Mesh(vertices, np.arange(12).reshape((-1, 3)))
    report, _, _ = audit_partition(mesh, (0, 3))
    assert [d["area_m2"] for d in report["domains"].values()] == pytest.approx([0, .5, .5, 1, 0])
    assert report["closure_residual_m2"] == pytest.approx(0)
    # Historical inclusive intervals double-count internal coplanar faces.
    assert sum(report["historical_inclusive_clip_area_m2"].values()) == pytest.approx(3)


def test_micron_negative_root_surface_not_tolerance_snapped():
    mesh = Mesh([[0, 0, -1e-7], [2, 0, -1e-7], [0, 2, -1e-7]], [[0, 1, 2]])
    report, _, _ = audit_partition(mesh)
    assert report["domains"]["below_span"]["area_m2"] == pytest.approx(2)
    assert report["three_region_area_m2"] == 0


def test_random_triangles_partition_independently_per_face():
    vertices = np.random.default_rng(4).uniform([-2, -2, -1], [2, 2, 4], (600, 3))
    mesh = Mesh(vertices, np.arange(600).reshape((-1, 3)))
    report, rows, _ = audit_partition(mesh, (0, 3))
    assert report["global_area_m2"] == pytest.approx(triangle_areas(mesh).sum())
    assert report["closure_residual_m2"] == pytest.approx(0, abs=1e-11)
    assert max(abs(row["closure_residual_m2"]) for row in rows) < 1e-12


def tetrahedron():
    return Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])


def test_closed_tetrahedron_and_open_triangle_topology():
    mesh = tetrahedron()
    report = audit_topology(mesh)
    assert report["closed_oriented_manifold_index_topology"] is True
    assert report["euler_characteristic_used_vertices"] == 2
    report = audit_topology(Mesh(mesh.vertices, [[0, 1, 2]]))
    assert report["boundary_edge_count"] == 3
    assert report["invalid_vertex_link_count"] == 0
    assert report["closed_oriented_manifold_index_topology"] is False


def test_nonmanifold_edge_and_pinched_vertex_detected():
    mesh = tetrahedron()
    duplicate = Mesh(mesh.vertices, np.vstack([mesh.faces, mesh.faces[0]]))
    assert audit_topology(duplicate)["nonmanifold_edge_count"] == 3
    # Two tetrahedra share a vertex but no edges: edge incidence alone misses it.
    vertices = np.vstack([mesh.vertices, -mesh.vertices[1:]])
    faces = np.vstack([mesh.faces, np.array([0, 4, 5, 6])[mesh.faces]])
    report = audit_topology(Mesh(vertices, faces))
    assert report["nonmanifold_edge_count"] == 0
    assert report["invalid_vertex_link_ids"] == [0]
    assert report["closed_oriented_manifold_index_topology"] is False


def test_metadata_requires_truth_hash_and_common_identity():
    manifest = {"file_sha256": "abc", "algorithm_input": False, "target": "T1/B1", "t_ref_sim_time_s": 1,
                "coordinate_frame": "blade_root_local", "units": "m", "surface_scope": "full"}
    score = {"metadata": {"model": dict(manifest), "truth": dict(manifest)},
             "file_signatures": {"truth": {"sha256": "abc"}}, "metadata_errors": []}
    assert audit_metadata(manifest, "abc", {"saved": score})["status"] == "MATCHED_SAVED_RECORDS"
    assert audit_metadata(manifest, "changed", {"saved": score})["status"] == "MISMATCH"
    score["metadata"]["model"]["t_ref_sim_time_s"] = 2
    assert audit_metadata(manifest, "abc", {"saved": score})["status"] == "MISMATCH"


def test_nonempty_output_refused_before_reading_inputs(tmp_path):
    output = tmp_path / "diagnostic"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("existing evidence")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        run_audit(tmp_path / "not_read.json", output)
    assert sentinel.read_text() == "existing evidence"
