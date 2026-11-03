"""Area measure, mesh subdivision, fixed material motion and nested budgets."""
import numpy as np
import pytest
from wfrl.radar_x100 import sampling, simulation


def surface(subdivision=1, time=0.):
    vertices, triangles = [], []
    for side, n in ((-1, 1), (1, subdivision)):
        for y in range(n):
            for z in range(n):
                start = len(vertices)
                yy = side * 2 + y / n
                zz = z / n - .5
                vertices.extend([[10 + time, yy, zz], [10 + time, yy + 1/n, zz],
                                 [10 + time, yy + 1/n, zz + 1/n], [10 + time, yy, zz + 1/n]])
                triangles.extend([[start, start+1, start+2], [start, start+2, start+3]])
    return simulation.SceneSample(time, 'test', f'subdivision-{subdivision}',
        np.array(vertices), np.array(triangles), np.eye(4), 'two equal area patches')


def test_equal_area_patches_remain_balanced_after_subdivision():
    for n in (1, 3, 12):
        sample = surface(n)
        plan = sampling.make_plan(sample, 4096)
        assert abs(np.mean(plan.points(sample)[:, 1] > 0) - .5) < .025
        assert not plan.barycentric.flags.writeable
        assert not plan.triangle_ids.flags.writeable


def test_nested_budgets_and_transport_preserve_material_coordinates():
    a = sampling.make_plan(surface(3), 128)
    b = sampling.make_plan(surface(3), 512)
    np.testing.assert_array_equal(a.triangle_ids, b.triangle_ids[:128])
    np.testing.assert_array_equal(a.barycentric, b.barycentric[:128])
    np.testing.assert_allclose(a.points(surface(3, 1)) - a.points(surface(3)), np.tile([1, 0, 0], (128, 1)))
    calls = []
    def material(t, ids, *, barycentric):
        s = surface(3, t)
        calls.append((t, ids.copy(), barycentric.copy()))
        return simulation.MaterialSample(t, s.source_id, s.geometry_id,
            np.einsum('nij,ni->nj', s.vertices[s.triangles[ids]], barycentric), s.world_from_sensor)
    kw = dict(sensor_id='radar', session_id='test', sequence=1, interval_s=(0, 2),
              candidate_budget=128, sampling_plan=a)
    full = simulation.simulate(lambda t: surface(3, t), 1, **kw)
    subset = simulation.simulate(lambda t: surface(3, t), 1, material_at=material, **kw)
    assert full.points.tobytes() == subset.points.tobytes()
    np.testing.assert_array_equal(full.radial_velocity_m_s, subset.radial_velocity_m_s)
    assert full.provenance['sampling_plan_id'] == a.identity and len(calls) == 2
    with pytest.raises(ValueError, match='profile'):
        simulation.simulate(lambda t: surface(3, t), 1, **(kw | dict(candidate_budget=256)))


def test_degenerate_reference_is_rejected():
    s = surface()
    s.vertices.setflags(write=True)
    s.vertices[:] = 0
    with pytest.raises(ValueError, match='area'):
        sampling.make_plan(s)
