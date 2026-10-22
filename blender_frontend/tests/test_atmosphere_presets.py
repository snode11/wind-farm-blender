import unittest
from types import SimpleNamespace

from wfrl_blender.atmosphere import PRESETS, apply_preset, get_preset
from wfrl_blender.overlays import overlay_lines


class AtmospherePresetTests(unittest.TestCase):
    def test_three_presets_have_distinct_visual_parameters(self):
        self.assertEqual(set(PRESETS), {"clear", "overcast", "dusk"})
        self.assertEqual(len({preset.world_color for preset in PRESETS.values()}), 3)
        self.assertEqual(len({preset.sun_color for preset in PRESETS.values()}), 3)

    def test_apply_preset_only_changes_visual_metadata(self):
        scene = {"simulation_value": 12.0}
        apply_preset(scene, "dusk", enabled=False, quality="render")
        self.assertEqual(scene["simulation_value"], 12.0)
        self.assertEqual(scene["wfrl_atmosphere_preset"], "dusk")
        self.assertFalse(scene["wfrl_atmosphere_enabled"])
        self.assertEqual(scene["wfrl_atmosphere_quality"], "render")

    def test_unknown_preset_and_overlay_provenance(self):
        with self.assertRaises(ValueError):
            get_preset("storm")
        lines = overlay_lines(time_s=2, wind_speed_mps=8, backend="FAST.Farm",
                              fidelity="EXPORTED", power_mw=1.2,
                              status="RUNNING", wake={"source": "FAST.Farm DisXY", "fidelity": "EXPORTED"})
        self.assertIn("DATA    EXPORTED", lines)
        self.assertIn("WAKE    FAST.Farm DisXY / EXPORTED", lines)


if __name__ == "__main__":
    unittest.main()
