"""Pure distribution rules for the Part 5 procedural grassland."""

import unittest

from wfrl_blender.landscape import lowland_factor, road_clearance, road_y, vegetation_factor


class LandscapeDistributionTests(unittest.TestCase):
    def test_road_and_maintenance_pads_are_clear(self):
        self.assertEqual(road_clearance(250.0, road_y(250.0)), 0.0)
        self.assertEqual(road_clearance(504.0, -11.5), 0.0)
        self.assertEqual(vegetation_factor(250.0, road_y(250.0), -5.0), 0.0)

    def test_verge_transitions_from_sparse_to_full_density(self):
        y = road_y(250.0)
        near = vegetation_factor(250.0, y + 18.0, -5.0)
        far = vegetation_factor(250.0, y + 70.0, -5.0)
        self.assertGreater(near, 0.0)
        self.assertLess(near, far)

    def test_low_ground_is_weighted_more_heavily(self):
        self.assertGreater(lowland_factor(-5.0), lowland_factor(18.0))
        low = vegetation_factor(250.0, road_y(250.0) + 80.0, -5.0)
        high = vegetation_factor(250.0, road_y(250.0) + 80.0, 18.0)
        self.assertGreater(low, high)


if __name__ == "__main__":
    unittest.main()
