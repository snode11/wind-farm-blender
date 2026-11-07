"""Independent projection/union examples and the NREL absolute clock."""
import importlib
import unittest
from test_nrel_defects import ALIAS
visibility=importlib.import_module(ALIAS+'.nrel_defects.defect_visibility')
timeline=importlib.import_module(ALIAS+'.nrel_defects.defect_timeline')

class NrelProjectionTests(unittest.TestCase):
    def test_shared_and_overlapping_triangles_have_exact_union_area(self):
        square=[[(0,0),(2,0),(2,2)],[(0,0),(2,2),(0,2)]]
        shifted=[[(x+1,y+1) for x,y in p] for p in square]
        self.assertAlmostEqual(visibility.union_area(square*2),4.)
        self.assertAlmostEqual(visibility.union_area(square+shifted),7.)
        diamond=[(-1,0),(0,1),(1,0),(0,-1)]
        self.assertAlmostEqual(visibility.union_area([diamond,[(-.5,-.5),(.5,-.5),(.5,.5),(-.5,.5)]]),2.)

    def test_half_occlusion_and_subpixel_width_remain_distinct_from_recognition(self):
        camera=dict(origin=(0,0,5),axes=((1,0,0),(0,1,0),(0,0,-1)),fx=100.,fy=100.,cx=49.5,cy=49.5,width=100,height=100,near=.01,far=100)
        a,b,c,d=(-.002,-1,0),(.002,-1,0),(.002,1,0),(-.002,1,0)
        support=dict(triangles=[(a,b,c),(a,c,d)],normals=[(0,0,1)]*2,width_sections=[(a,b)],centerline=[(0,-1,0),(0,1,0)])
        def blocker(origin,direction,maximum):
            hit=(1-origin[2])/direction[2]
            return hit if direction[0]<0 and hit<maximum else None
        row=visibility.analyze_support(support,camera,blocker,sampling_step_px=.01)
        self.assertEqual(row['status'],'PARTIAL')
        self.assertAlmostEqual(row['visible_fraction_in_frame'],.5,places=6)
        self.assertAlmostEqual(row['width_px']['max'],.08)
        self.assertEqual(row['appearance_review']['status'],'NOT_REVIEWED')

    def test_source_start_maps_117_seconds_to_frame_one(self):
        mapping=timeline.FrameMapping(117.,.1,20.,60.,117.)
        self.assertEqual(mapping.sample(0)['blender_frame'],1)
        self.assertEqual(mapping.sample(1)['blender_frame'],4)
        self.assertEqual(mapping.to_dict()['blender_start_time_s'],117.)
        with self.assertRaises(ValueError):timeline.FrameMapping(116.,1.,20.,60.,117.)



# The gate fixture uses sensor dimensions and image aspect, independently of K.
def _ordinary_camera(monkeypatch, width=1001, height=1000, percentage=50,
                     sensor_fit='HORIZONTAL', pixel_aspect=(1., 1.), shift=(0., 0.)):
    import sys
    from types import SimpleNamespace

    class Vector(tuple):
        def normalized(self):
            return self

    class World:
        translation = (0., 0., 0.)

        def copy(self):
            return self

        def to_3x3(self):
            return self

        def __matmul__(self, vector):
            return vector

        def __iter__(self):
            return iter(((1., 0., 0., 0.), (0., 1., 0., 0.),
                         (0., 0., 1., 0.), (0., 0., 0., 1.)))

    def gate(w, h, sx, sy):
        fit = sensor_fit
        if fit == 'AUTO':
            fit = 'HORIZONTAL' if w * sx >= h * sy else 'VERTICAL'
        sensor = 24. if sensor_fit == 'VERTICAL' else 36.
        view = w if fit == 'HORIZONTAL' else h * sy / sx
        unit = sensor / (50. * view)
        return ((-w / 2 + shift[0] * view) * unit,
                (w / 2 + shift[0] * view) * unit,
                (-h * sy / sx / 2 + shift[1] * view) * unit,
                (h * sy / sx / 2 + shift[1] * view) * unit)

    render = SimpleNamespace(resolution_x=width, resolution_y=height,
                             resolution_percentage=percentage,
                             pixel_aspect_x=pixel_aspect[0], pixel_aspect_y=pixel_aspect[1])
    scene = SimpleNamespace(render=render)
    deps = object()
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(context=SimpleNamespace(
        scene=scene, evaluated_depsgraph_get=lambda: deps)))
    monkeypatch.setitem(sys.modules, 'mathutils', SimpleNamespace(Vector=Vector))
    monkeypatch.setitem(sys.modules, ALIAS + '.custom_cameras', SimpleNamespace())

    class Camera(dict):
        matrix_world = World()
        data = SimpleNamespace(type='PERSP', clip_start=.1, clip_end=100.)

        def evaluated_get(self, graph):
            assert graph is deps
            return self

        def calc_matrix_camera(self, graph, *, x, y, scale_x, scale_y):
            assert graph is deps
            assert (x, y) == (round(width * percentage / 100), round(height * percentage / 100))
            assert (scale_x, scale_y) == pixel_aspect
            left, right, bottom, top = gate(x, y, scale_x, scale_y)
            return ((2 / (right-left), 0., (right+left)/(right-left), 0.),
                    (0., 2 / (top-bottom), (top+bottom)/(top-bottom), 0.),
                    (0., 0., -100.1/99.9, -20./99.9), (0., 0., -1., 0.))

    left, right, bottom, top = gate(width, height, *pixel_aspect)
    Camera.data.view_frame = lambda scene: [SimpleNamespace(x=x, y=y, z=-1.)
        for x, y in ((left, bottom), (right, bottom), (right, top), (left, top))]
    camera = Camera()
    w, h = round(width * percentage / 100), round(height * percentage / 100)
    fx, fy = w / (right-left), h / (top-bottom)
    legacy = ((fx, 0., -.5-left*fx), (0., fy, -.5+top*fy), (0., 0., 1.))
    return visibility._camera_descriptor(camera, scene), legacy


def test_ordinary_camera_rounded_resolution_matches_projection(monkeypatch):
    import pytest
    descriptor, legacy = _ordinary_camera(monkeypatch)
    expected = descriptor['P'][1][1] * descriptor['height'] / 2
    assert legacy[1][1] - expected == pytest.approx(25 / 36)
    assert descriptor['fy'] == pytest.approx(expected, rel=1e-9)


def test_ordinary_camera_exact_scale_preserves_intrinsics(monkeypatch):
    import numpy as np
    for width, height, percentage in ((1001, 1000, 100), (1000, 800, 50)):
        descriptor, legacy = _ordinary_camera(monkeypatch, width, height, percentage)
        np.testing.assert_allclose(descriptor['K'], legacy, rtol=1e-14, atol=1e-12)


def test_ordinary_camera_pixel_centres_and_borders_match_projection(monkeypatch):
    import numpy as np
    import pytest
    for fit in ('HORIZONTAL', 'VERTICAL', 'AUTO'):
        for aspect in ((1., 1.), (2., .7), (.8, 1.8)):
            for shift in ((0., 0.), (.17, -.23), (-.19, .31)):
                d, _ = _ordinary_camera(monkeypatch, sensor_fit=fit, pixel_aspect=aspect, shift=shift)
                inverse = np.linalg.inv(d['K'])
                for u, v in ((0., 0.), (d['width']-1., d['height']-1.),
                             (-.5, -.5), (d['width']-.5, d['height']-.5), (123.25, 87.75)):
                    ray = inverse @ (u, v, 1.)
                    clip = np.array(d['P']) @ (ray[0], -ray[1], -ray[2], 1.)
                    pixel = ((clip[0]/clip[3]+1)*d['width']/2-.5,
                             (1-clip[1]/clip[3])*d['height']/2-.5)
                    assert pixel == pytest.approx((u, v), rel=1e-9, abs=1e-9)


if __name__ == '__main__':
    unittest.main()
