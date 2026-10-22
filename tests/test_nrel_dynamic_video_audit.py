"""Safety-relevant time/status contracts for the independent DY1 audit."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/nrel_dynamic_video_audit.py"
SPEC = importlib.util.spec_from_file_location("dynamic_audit", SOURCE)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class DynamicVideoAuditTests(unittest.TestCase):
    def test_valid_pts_and_dropped_or_shifted_frames(self):
        frames = [{"best_effort_timestamp_time": i / 20} for i in range(100)]
        self.assertEqual(len(audit.validate_pts(frames, 100, 20)), 100)
        with self.assertRaises(ValueError):
            audit.validate_pts(frames[:-1], 100, 20)
        frames[42] = {"best_effort_timestamp_time": 2.15}
        with self.assertRaises(ValueError):
            audit.validate_pts(frames, 100, 20)
        frames[42] = {"best_effort_timestamp_time": float("nan")}
        with self.assertRaises(ValueError):
            audit.validate_pts(frames, 100, 20)

    def test_rgb_presence_does_not_establish_target_measurement(self):
        row = audit.unreviewed_row("C3", 43, 2.15, 122.175)
        for key in ("b1_identity", "image_visible", "unoccluded", "trusted_contour",
                    "complete_surface_tip_visible"):
            self.assertEqual(row[key], "UNREVIEWED")
        self.assertEqual(row["structural_point_constraint"], "NOT_ESTABLISHED")
        self.assertEqual(row["d_constraint"], "NOT_ESTABLISHED")
        self.assertIsNone(row["d_m"])


if __name__ == "__main__":
    unittest.main()
