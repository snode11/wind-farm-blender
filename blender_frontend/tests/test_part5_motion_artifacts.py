from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/blender"))
from part5_motion import frame_timeline, inspect_frames  # noqa: E402


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\x0dIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
)


class Part5MotionArtifactTests(unittest.TestCase):
    def test_timeline_is_explicit_and_bounded(self):
        self.assertEqual(frame_timeline(0), 301)
        self.assertEqual(frame_timeline(35), 371)
        with self.assertRaises(ValueError):
            frame_timeline(36)

    def test_missing_and_wrong_size_frames_are_not_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000.png").write_bytes(PNG_1X1)
            report = inspect_frames(root, expected_size=(1, 1), count=2)
            self.assertEqual(report.valid, (0,))
            self.assertEqual(report.missing, (1,))
            self.assertFalse(report.complete)
            (root / "001.png").write_bytes(b"not a png")
            report = inspect_frames(root, expected_size=(1, 1), count=2)
            self.assertEqual(report.invalid, (1,))
            self.assertFalse(report.complete)


if __name__ == "__main__":
    unittest.main()
