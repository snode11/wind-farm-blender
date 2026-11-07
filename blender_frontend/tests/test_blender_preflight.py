from pathlib import Path
import sys
import tempfile
import unittest

from scripts.blender.blender_preflight import (
    BlenderLaunchBlocked,
    inspect_blender,
    require_launchable,
)


class BlenderPreflightTests(unittest.TestCase):
    def test_seatbelt_is_blocked_before_process_start(self):
        result = inspect_blender(sys.executable, env={"CODEX_SANDBOX": "seatbelt"})
        self.assertTrue(result.restricted)
        self.assertFalse(result.launchable)
        self.assertIn("Metal capability detection", result.reason)
        with self.assertRaises(BlenderLaunchBlocked):
            require_launchable(sys.executable, env={"CODEX_SANDBOX": "seatbelt"})

    def test_normal_environment_allows_an_executable(self):
        result = inspect_blender(sys.executable, env={})
        self.assertTrue(result.launchable)
        self.assertEqual(require_launchable(sys.executable, env={}).executable, sys.executable)

    def test_missing_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-blender"
            result = inspect_blender(path, env={})
            self.assertFalse(result.launchable)
            self.assertIn("does not exist", result.reason)


if __name__ == "__main__":
    unittest.main()
