import unittest
from array import array
from wfrl_blender import overlays


class SceneOverlayTests(unittest.TestCase):
    def test_blender_compatible_numeric_array(self):
        self.assertIn('POWER   3.00 MW', overlays.scene_overlay({'wfrl_power_mw': array('d', [1, 2])}))
