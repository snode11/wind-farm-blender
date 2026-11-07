import unittest

from wfrl_blender.sensors import SensorPayloadError, frustum_vertices


class SensorGeometryTests(unittest.TestCase):
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
