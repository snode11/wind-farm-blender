import unittest

from wfrl_blender.sensors import SensorPayloadError, frustum_vertices, lidar_rays


class SensorGeometryTests(unittest.TestCase):
    def test_lidar_beams_have_expected_count_and_endpoints(self):
        rays = lidar_rays((0, 0, 0), (-1, 0, 0), (50, 100), half_angle_deg=15)
        self.assertEqual(len(rays), 10)
        self.assertEqual(rays[0].endpoint, (-50.0, 0.0, 0.0))
        self.assertAlmostEqual(sum(value * value for value in rays[1].direction), 1.0)
        self.assertEqual(rays[-1].range_m, 100.0)

    def test_frustum_order_and_rejects_bad_ranges(self):
        vertices = frustum_vertices((0, 0, 0), (1, 0, 0), (0, 0, 1),
                                    fov_deg=90, near_m=1, far_m=10)
        self.assertEqual(len(vertices), 8)
        self.assertAlmostEqual(vertices[0][0], 1.0)
        self.assertAlmostEqual(vertices[4][0], 10.0)
        with self.assertRaises(SensorPayloadError):
            frustum_vertices((0, 0, 0), (0, 0, 0), (0, 0, 1), fov_deg=90, near_m=1, far_m=2)


if __name__ == "__main__":
    unittest.main()
