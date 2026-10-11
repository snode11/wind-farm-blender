"""Known geometry tests; these do not need Blender or render images."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gw184.defect_timeline import FrameMapping, summarize_intervals
from gw184.defect_visibility import analyze_support, union_area, clip_polygon, AnalysisCancelled


def camera():
    return dict(origin=(0, 0, 5), axes=((1, 0, 0), (0, 1, 0), (0, 0, -1)),
                fx=100., fy=100., cx=49.5, cy=49.5, width=100, height=100, near=.01, far=100)


def square(x0=-1., x1=1., y0=-1., y1=1., z=0., normal=(0, 0, 1)):
    a, b, c, d = (x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)
    return dict(defect_id='D1', revision=1, blade_id=1, support_id='D1:r1:opening_footprint',
                support_kind='opening_footprint', primary=True, support_definition={'version': 'test-v1'},
                triangles=[(a, b, c), (a, c, d)], normals=[normal, normal],
                centerline=[((x0+x1)/2, y0, z), ((x0+x1)/2, y1, z)],
                width_sections=[((x0, 0, z), (x1, 0, z))])


class AreaTests(unittest.TestCase):
    def test_triangle_union_does_not_count_shared_or_overlapping_area_twice(self):
        square_polys = [[(0, 0), (2, 0), (2, 2)], [(0, 0), (2, 2), (0, 2)]]
        self.assertAlmostEqual(union_area(square_polys), 4.)
        self.assertAlmostEqual(union_area(square_polys * 2), 4.)
        shifted = [[(x+1, y+1) for x, y in p] for p in square_polys]
        self.assertAlmostEqual(union_area(square_polys + shifted), 7.)

    def test_crossing_edges_and_disjoint_union(self):
        diamond = [(-1, 0), (0, 1), (1, 0), (0, -1)]
        middle = [(-.5, -.5), (.5, -.5), (.5, .5), (-.5, .5)]
        self.assertAlmostEqual(union_area([diamond, middle]), 2.)
        self.assertAlmostEqual(union_area([diamond, [(x+3, y) for x, y in diamond]]), 4.)

    def test_pixel_border_clipping(self):
        poly = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        self.assertAlmostEqual(union_area([clip_polygon(poly, (-.5, -.5, .5, .5))]), 1.)

    def test_opening_is_visible_when_ray_would_hit_deeper_groove_floor(self):
        calls = []
        def bottom(origin, direction, maximum):
            hit = (-.25 - origin[2]) / direction[2]
            calls.append((hit, maximum))
            return hit if hit < maximum else None
        row = analyze_support(square(), camera(), bottom, sampling_step_px=2.)
        self.assertEqual(row['status'], 'VISIBLE')
        self.assertAlmostEqual(row['candidate_area_px2'], 1600.)
        self.assertAlmostEqual(row['visible_area_px2'], 1600.)
        self.assertTrue(all(hit > maximum for hit, maximum in calls))
        self.assertEqual(row['appearance_review']['status'], 'NOT_REVIEWED')

    def test_front_blocker_covers_half_and_backface_rejected(self):
        def half_blocker(origin, direction, maximum):
            hit = (1 - origin[2]) / direction[2]
            x = origin[0] + direction[0] * hit
            return hit if x < 0 and hit < maximum else None
        row = analyze_support(square(), camera(), half_blocker, sampling_step_px=1.)
        self.assertEqual(row['status'], 'PARTIAL')
        self.assertAlmostEqual(row['visible_fraction_in_frame'], .5, places=8)
        row = analyze_support(square(normal=(0, 0, -1)), camera(), lambda *args: None)
        self.assertEqual(row['status'], 'OCCLUDED')
        self.assertEqual(row['reasons'], ['BACK_FACING_SUPPORT'])

    def test_partial_frame_stays_partial_even_without_occluders(self):
        row = analyze_support(square(x0=2., x1=4.), camera(), lambda *args: None)
        self.assertEqual(row['status'], 'PARTIAL')
        self.assertAlmostEqual(row['in_frame_fraction'], .25)
        self.assertAlmostEqual(row['visible_fraction_in_frame'], 1.)
        self.assertGreater(row['projected_bbox_px'][2], 99.5)
        self.assertAlmostEqual(row['visible_bbox_px'][2], 99.5)

    def test_subpixel_float_area_survives(self):
        row = analyze_support(square(x0=-.0001, x1=.0001, y0=-.001, y1=.001), camera(), lambda *args: None)
        self.assertEqual(row['status'], 'VISIBLE')
        self.assertGreater(row['visible_area_px2'], 0)
        self.assertLess(row['visible_area_px2'], 1)
        self.assertAlmostEqual(row['width_px']['max'], .004, places=8)

    def test_outside_empty_denominator_near_plane_and_cancel(self):
        row = analyze_support(square(x0=10, x1=11), camera(), lambda *args: None)
        self.assertEqual(row['status'], 'OUT_OF_FRAME')
        self.assertIsNone(row['visible_fraction_in_frame'])
        row = analyze_support(square(z=4.999), camera(), lambda *args: None)
        self.assertEqual(row['status'], 'INVALID')
        self.assertIsNone(row['visible_area_px2'])
        with self.assertRaises(AnalysisCancelled):
            analyze_support(square(), camera(), lambda *args: None, cancel=lambda: True)

    def test_duplicate_projected_surface_visible_union_is_not_added(self):
        support = square()
        support['triangles'] *= 2
        support['normals'] *= 2
        row = analyze_support(support, camera(), lambda *args: None, sampling_step_px=2)
        self.assertAlmostEqual(row['visible_area_px2'], 1600.)


class TimelineTests(unittest.TestCase):
    def test_full_job_endpoint_is_not_a_video_frame(self):
        mapping = FrameMapping(0, 320, 30, 30)
        self.assertEqual(mapping.frame_count, 9600)
        self.assertEqual(mapping.sample(0)['blender_frame'], 1)
        self.assertEqual(mapping.sample(9599)['blender_frame'], 9600)
        self.assertAlmostEqual(mapping.sample(9599)['time_s'], 9599 / 30)
        endpoint = mapping.current(320)
        self.assertIsNone(endpoint['frame_index'])
        self.assertEqual(endpoint['blender_frame'], 9601)
        self.assertFalse(endpoint['included_in_video'])

    def test_nonzero_start_subframe_offgrid_and_validation(self):
        mapping = FrameMapping(100, .1, 30, 24)
        self.assertEqual(mapping.sample(0)['blender_frame'], 2401)
        self.assertAlmostEqual(mapping.sample(1)['blender_subframe'], .8)
        self.assertIsNone(mapping.current(100.01)['frame_index'])
        for args in ((0, .01, 30, 30), (-1, 1, 30, 30), (0, 1, 0, 30), (True, 1, 30, 30)):
            with self.assertRaises(ValueError):
                FrameMapping(*args)
        with self.assertRaises(ValueError):
            mapping.sample(3)

    def test_interval_summary_retains_gap_and_partial_scan(self):
        mapping = FrameMapping(100, 1, 30, 30)
        rows = []
        for index, status in ((0, 'VISIBLE'), (1, 'PARTIAL'), (3, 'VISIBLE'), (4, 'OCCLUDED')):
            rows.append(dict(defect_id='D1', revision=1, support_id='S1', camera_id='C1',
                             frame_index=index, time_s=mapping.sample(index)['time_s'],
                             status=status, visible_area_px2=10-index))
        summary = summarize_intervals(rows, mapping, False)[0]
        self.assertEqual(summary['status'], 'INCOMPLETE')
        self.assertEqual(len(summary['visible_intervals']), 2)
        self.assertEqual(summary['visible_intervals'][0]['last_frame'], 1)
        self.assertEqual(summary['visible_intervals'][0]['candidate_best_frame'], 0)


if __name__ == '__main__':
    unittest.main()
