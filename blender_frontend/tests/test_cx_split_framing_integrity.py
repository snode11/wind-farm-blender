"""Finite framing inputs and rollback of caller-owned viewport state."""
from contextlib import nullcontext
import math
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfrl_blender import split_reconstruction as split


class Vector(np.ndarray):
    @property
    def length(self):
        return float(np.linalg.norm(self))


def vector(values):
    return np.asarray(values, dtype=float).view(Vector)


class Rotation:
    def copy(self):
        return Rotation()

    def inverted(self):
        return self.copy()

    def __matmul__(self, point):
        return vector(point)


@pytest.fixture
def viewport(monkeypatch):
    rv = NS(view_perspective='CAMERA', view_location=vector((3., 4., 5.)),
            view_rotation=Rotation(), view_distance=77., view_camera_zoom=43.,
            view_camera_offset=(.2, -.3), window_matrix=np.diag([1.8, 2.2, -1., 0.]),
            update=lambda: None)
    camera = NS(name='SplitRecon.Camera', data=NS(type='ORTHO', ortho_scale=186.),
                matrix_world=NS(translation=vector((200., 20., 125.)), to_quaternion=Rotation))
    space = NS(region_3d=rv, camera=camera, lens=61., clip_start=.1)
    area = NS(type='VIEW_3D', width=400, height=600, spaces=NS(active=space),
              regions=[NS(type='WINDOW', width=400, height=600)], tag_redraw=lambda: None)
    context = NS(preferences=NS(system=NS(ui_scale=1.), view=NS(smooth_view=200)),
                 view_layer=NS(update=lambda: None), temp_override=lambda **kw: nullcontext())
    def view_camera():
        rv.view_perspective = 'CAMERA'
        return {'FINISHED'}
    bpy = NS(context=context, ops=NS(view3d=NS(view_camera=view_camera)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'mathutils', NS(Vector=vector))
    monkeypatch.setattr(split, '_rectangle', lambda area: (0., 0., area.width, area.height))
    return NS(area=area, space=space, rv=rv, camera=camera, bpy=bpy)


def navigation(space):
    state = split._navigation(space)
    state.pop('view_rotation')
    state['view_location'] = tuple(state['view_location'])
    return state


@pytest.mark.parametrize('field,value', [('points', [(float('nan'), 0., 0.)]),
                                       ('points', [(0., 0., float('inf'))]),
                                       ('scale_x', float('nan')), ('scale_y', float('inf')),
                                       ('bounds', (-1., -1., float('nan'), 1.)),
                                       ('bounds', (-1., -1., 1., float('inf'))),
                                       ('near', float('nan')), ('near', float('inf'))])
def test_perspective_fit_rejects_nonfinite_inputs(field, value):
    arguments = dict(points=[(0., 0., 0.)], scale_x=1.8, scale_y=2.2,
                     bounds=(-1., -1., 1., 1.), near=.1)
    arguments[field] = value
    with pytest.raises(ValueError):
        split._perspective_distance(**arguments)


def test_degenerate_finite_bounds_have_a_finite_positive_distance():
    distance, offset = split._perspective_distance([(0., 0., 0.)], 1.8, 2.2, (-1., -1., 1., 1.))
    assert distance == 1.06
    assert offset == (0., 0., 0.)


@pytest.mark.parametrize('bounds', [(0., 0., 0., float('nan'), 1., 1.),
                                   (2., 0., 0., 1., 1., 1.), (1., 2.)])
def test_saved_texture_bounds_reject_invalid_values(bounds):
    obj = NS(get=lambda key: bounds, data=NS(shape_keys=None),
             bound_box=split._corners((0., 0., 0.), (1., 1., 1.)))
    with pytest.raises(ValueError):
        split._saved_corners(obj)


def test_nonfinite_shape_keys_do_not_poison_bounds_cache(monkeypatch):
    def read(_name, values):
        values[:] = [float('nan'), 0., 0.]
    keys = NS(as_pointer=lambda: 2, key_blocks=[NS(data=NS(foreach_get=read))])
    obj = NS(data=NS(as_pointer=lambda: 1, shape_keys=keys, vertices=[object()]))
    monkeypatch.setattr(split, '_BOUNDS', {})
    with pytest.raises(ValueError):
        split._saved_corners(obj)
    assert split._BOUNDS == {}


def test_empty_shape_key_mesh_has_no_framing_points(monkeypatch):
    keys = NS(as_pointer=lambda: 2, key_blocks=[NS(data=NS(foreach_get=lambda *_: None))])
    obj = NS(data=NS(as_pointer=lambda: 1, shape_keys=keys, vertices=[]))
    monkeypatch.setattr(split, '_BOUNDS', {})
    assert not split._saved_corners(obj)
    assert not split._BOUNDS


@pytest.mark.parametrize('bad', [None, NS(x=float('nan'), y=1.), NS(x=1., y=float('inf'))])
def test_projection_does_not_silently_fit_a_subset(viewport, monkeypatch, bad):
    projections = iter([NS(x=0., y=0.), bad])
    monkeypatch.setitem(sys.modules, 'bpy_extras.view3d_utils', NS(
        location_3d_to_region_2d=lambda *_: next(projections)))
    assert split._project(viewport.area, [vector((0., 0., 0.)), vector((1., 1., 1.))]) is None


@pytest.mark.parametrize('invalid', ['width', 'height', 'region', 'nan'])
def test_free_fit_rejects_invalid_canvas_or_points_without_moving_view(viewport, invalid):
    before = navigation(viewport.space)
    points = [vector((-1., -1., -1.)), vector((1., 1., 1.))]
    if invalid in ('width', 'height'):
        setattr(viewport.area.regions[0], invalid, 0)
    elif invalid == 'region':
        viewport.area.regions.clear()
    else:
        points[0][0] = float('nan')
    with pytest.raises(ValueError):
        split._fit_free(viewport.area, points)
    assert navigation(viewport.space) == before


def test_free_fit_restores_navigation_on_update_failure(viewport):
    before = navigation(viewport.space)
    def fail_update():
        raise RuntimeError('viewport update failed')
    viewport.rv.update = fail_update
    with pytest.raises(RuntimeError, match='viewport update failed'):
        split._fit_free(viewport.area, [vector((-1., -1., -1.)), vector((1., 1., 1.))])
    assert navigation(viewport.space) == before


@pytest.mark.parametrize('failure_call', [1, 2, 3, 4])
def test_camera_fit_restores_navigation_and_optics_when_projection_fails(viewport, monkeypatch, failure_call):
    before = navigation(viewport.space)
    scale = viewport.camera.data.ortho_scale
    calls = []
    def project(*_):
        calls.append(1)
        return None if len(calls) == failure_call else (0., 0., 10_000., 10_000.)
    monkeypatch.setattr(split, '_project', project)
    with pytest.raises(ValueError):
        split._fit_camera(viewport.area, [vector((1., 1., 1.))], orbit_center=(0., 0., 0.))
    assert navigation(viewport.space) == before
    assert viewport.camera.data.ortho_scale == scale
    assert viewport.bpy.context.preferences.view.smooth_view == 200


@pytest.mark.parametrize('result', ['raise', 'cancel'])
def test_camera_operator_failure_restores_view_and_preferences(viewport, monkeypatch, result):
    before = navigation(viewport.space)
    def view_camera():
        if result == 'raise':
            raise RuntimeError('camera operator failed')
        return {'CANCELLED'}
    viewport.bpy.ops.view3d.view_camera = view_camera
    monkeypatch.setattr(split, '_project', lambda *_: (0., 0., 100., 100.))
    with pytest.raises(RuntimeError):
        split._fit_camera(viewport.area, [vector((1., 1., 1.))], orbit_center=(0., 0., 0.))
    assert navigation(viewport.space) == before
    assert viewport.bpy.context.preferences.view.smooth_view == 200


def test_successful_camera_fit_keeps_existing_zoom_and_offset_formula(viewport, monkeypatch):
    viewport.camera.name = 'Source.Camera'
    viewport.camera.get = lambda _: False
    def project(area, _points):
        dx, dy = area.spaces.active.region_3d.view_camera_offset
        return 10. + dx * 1000, 20. + dy * 1000, 210. + dx * 1000, 320. + dy * 1000
    monkeypatch.setattr(split, '_project', project)
    split._fit_camera(viewport.area, [vector((1., 1., 1.))], orbit_center=(0., 0., 0.))
    assert viewport.rv.view_camera_zoom == (math.sqrt(2 * (496. / 300.)) - math.sqrt(2)) * 50
    assert viewport.rv.view_camera_offset == (.09, .098)
    assert viewport.camera.data.ortho_scale == 186.


def test_pair_fit_rolls_back_left_when_right_fails(viewport, monkeypatch):
    from wfrl_blender import split_turbine_context
    right_rv = NS(**vars(viewport.rv))
    right_space = NS(**{**vars(viewport.space), 'region_3d': right_rv})
    right = NS(**{**vars(viewport.area), 'spaces': NS(active=right_space)})
    before = navigation(viewport.space)
    def fit_free(area, _points):
        area.spaces.active.region_3d.view_distance = 999.
    def fit_camera(*_args, **_kwargs):
        raise ValueError('right projection failed')
    monkeypatch.setattr(split, '_fit_free', fit_free)
    monkeypatch.setattr(split, '_fit_camera', fit_camera)
    monkeypatch.setattr(split, '_points', lambda *_a, **_kw: [vector((1., 1., 1.))])
    monkeypatch.setattr(split, '_display_origin', lambda _: (0., 0., 0.))
    monkeypatch.setattr(split_turbine_context, 'rotor_center', lambda *_a, **_kw: (0., 0., 0.))
    with pytest.raises(ValueError, match='right projection failed'):
        split._fit_pair(NS(scene=object()), [viewport.area, right], explicit=True)
    assert navigation(viewport.space) == before


def test_sub_ulp_frustum_center_cannot_divide_by_zero():
    with pytest.raises(ValueError, match='尺寸'):
        split._perspective_distance([(0., 0., 0.)], 1., 1.,
                                    (1., -1., float(np.nextafter(1., 2.)), 1.))


def test_camera_fit_rejects_overflowing_projected_centers(viewport, monkeypatch):
    before = navigation(viewport.space)
    scale = viewport.camera.data.ortho_scale
    monkeypatch.setattr(split, '_project', lambda *_: (1e308, 1e308, 1.1e308, 1.1e308))
    with pytest.raises(ValueError):
        split._fit_camera(viewport.area, [vector((1., 1., 1.))], orbit_center=(0., 0., 0.))
    assert navigation(viewport.space) == before
    assert viewport.camera.data.ortho_scale == scale


def test_nonfinite_rotor_diameter_cannot_silently_shrink_framing_bounds(viewport):
    class Objects(list):
        def get(self, name):
            return next((obj for obj in self if obj.name == name), None)
    mesh = NS(type='MESH', name='WFRL.Turbine.T1.Blade1', matrix_world=np.eye(3),
              visible_get=lambda **_: True,
              evaluated_get=lambda _: NS(bound_box=[(1., 1., 1.), (2., 2., 2.)]))
    root = NS(type='EMPTY', name='WFRL.Turbine.T1', get=lambda *_: float('nan'))
    rotor = NS(type='EMPTY', name='WFRL.Turbine.T1.Rotor', matrix_world=NS(translation=vector((0., 0., 0.))))
    viewport.bpy.context.evaluated_depsgraph_get = lambda: None
    with pytest.raises(ValueError):
        split._points(NS(objects=Objects([mesh, root, rotor])), viewport.area, reconstruction=False)
