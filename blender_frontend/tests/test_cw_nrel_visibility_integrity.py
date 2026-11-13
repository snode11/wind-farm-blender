"""Invalid support data and scene ownership in ideal visibility analysis."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


@pytest.fixture
def projection_case(frontend_module):
    module = frontend_module('wfrl_blender.nrel_defects.defect_visibility')
    camera = dict(origin=(0., 0., 5.), axes=((1., 0., 0.), (0., 1., 0.), (0., 0., -1.)),
                  fx=10., fy=10., cx=9.5, cy=9.5, width=20, height=20, near=.01, far=100.)
    support = dict(triangles=[[(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]], normals=[(0., 0., 1.)])
    return module, support, camera


@pytest.mark.parametrize('component', ['vertex', 'normal'])
@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_support_is_invalid_without_visibility_measurement(projection_case, component, invalid):
    module, support, camera = projection_case
    if component == 'vertex':
        support['triangles'][0][0] = (invalid, 0., 0.)
    else:
        support['normals'][0] = (invalid, 0., 1.)
    ray = Mock(return_value=None)
    row = module.analyze_support(support, camera, ray)
    assert row['status'] == 'INVALID' and row['reasons']
    assert row['visible_area_px2'] is None and row['visible_fraction_in_frame'] is None
    assert row['view_angle_deg'] is None
    ray.assert_not_called()


@pytest.mark.parametrize('component', ['vertex', 'normal'])
def test_incomplete_support_vectors_are_invalid(projection_case, component):
    module, support, camera = projection_case
    if component == 'vertex':
        support['triangles'][0][0] = (0., 0.)
    else:
        support['normals'][0] = (0., 1.)
    row = module.analyze_support(support, camera, lambda *_args: None)
    assert row['status'] == 'INVALID' and row['visible_area_px2'] is None


def test_missing_widths_and_support_are_not_zero_measurements(projection_case):
    module, support, camera = projection_case
    row = module.analyze_support(support, camera, lambda *_args: None)
    assert row['status'] == 'VISIBLE'
    assert row['width_px']['min'] is row['width_px']['max'] is row['width_px']['mean'] is None
    assert row['width_px']['valid_coverage'] is None and row['length_px'] is None
    missing = module.analyze_support({}, camera, lambda *_args: None)
    assert missing['status'] == 'INVALID' and missing['visible_area_px2'] is None
    disabled = module.analyze_support(dict(support, enabled=False), camera, lambda *_args: None)
    assert disabled['status'] == 'NOT_EVALUATED' and disabled['visible_area_px2'] is None


@pytest.mark.parametrize('points', [[(0., 0., 0.)], [(float('nan'), 0., 0.), (0., 1., 0.)]])
def test_incomplete_or_nonfinite_centreline_stays_missing(projection_case, points):
    module, support, camera = projection_case
    support['centerline'] = points
    assert module.analyze_support(support, camera, lambda *_args: None)['length_px'] is None


def test_nonfinite_width_section_does_not_count_as_measured(projection_case):
    module, support, camera = projection_case
    support['width_sections'] = [((0., 0., 0.), (1., 0., 0.)),
                                 ((float('nan'), 0., 0.), (1., 0., 0.))]
    width = module.analyze_support(support, camera, lambda *_args: None)['width_px']
    assert width['values'] == [2.] and width['valid_sections'] == 1
    assert width['total_sections'] == 2 and width['valid_coverage'] == .5


@pytest.mark.parametrize('offset, expected', [(0., 'VISIBLE'), (4.5, 'PARTIAL'), (20., 'OUT_OF_FRAME')])
def test_frame_clipping_preserves_pixel_border_convention(projection_case, offset, expected):
    module, support, camera = projection_case
    support = deepcopy(support)
    support['triangles'] = [[(x + offset, y, z) for x, y, z in tri] for tri in support['triangles']]
    assert module.analyze_support(support, camera, lambda *_args: None)['status'] == expected


@pytest.mark.parametrize('mesh_fails', [False, True])
def test_scene_tree_uses_target_depsgraph_and_releases_mesh(frontend_module, monkeypatch, mesh_fails):
    module = frontend_module('wfrl_blender.nrel_defects.defect_visibility')
    import bpy
    foreign = object()
    scene = NS(view_layers=[object()])
    context = NS(scene=foreign, evaluated_depsgraph_get=lambda: context.scene)

    @contextmanager
    def override(**values):
        old = context.scene
        context.scene = values['scene']
        assert values['view_layer'] is scene.view_layers[0]
        try:
            yield
        finally:
            context.scene = old

    context.temp_override = override
    monkeypatch.setattr(bpy, 'context', context)
    mesh = NS(vertices=[], loop_triangles=[], calc_loop_triangles=Mock())
    if mesh_fails:
        mesh.calc_loop_triangles.side_effect = RuntimeError('triangulation failed')
    evaluated = NS(to_mesh=lambda: mesh, to_mesh_clear=Mock())
    graphs = []
    obj = NS(evaluated_get=lambda graph: graphs.append(graph) or evaluated)
    monkeypatch.setattr(module, '_renderable_objects', lambda _scene: [obj])
    if mesh_fails:
        with pytest.raises(RuntimeError, match='triangulation failed'):
            module.scene_tree({'scene': scene})
    else:
        assert module.scene_tree({'scene': scene}) is None
    assert graphs == [scene]
    evaluated.to_mesh_clear.assert_called_once_with()
    assert context.scene is foreign
