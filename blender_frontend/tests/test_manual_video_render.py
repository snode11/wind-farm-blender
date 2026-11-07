"""Deterministic frame-count and exclusive video publication contracts."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'wfrl_blender/manual_video_render.py'
SPEC = importlib.util.spec_from_file_location('manual_video_render', SOURCE)
video = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(video)


class ManualVideoTests(unittest.TestCase):
    def test_exact_half_open_clip(self):
        self.assertEqual(video.frame_count(8, 7, 25), 375)
        self.assertEqual(video.frame_count(.08, .04, 25), 3)
        self.assertEqual(video.frame_count(8, 0, 25), 200)

    def test_invalid_timing_rejected(self):
        for args in ((8, -.1, 25), (0, 7, 25), (8, float('nan'), 25),
                     (8, 7, 30), (8, 7, True), (8, .01, 25)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                video.frame_count(*args)

    def test_rna_single_precision_seconds_keep_integral_frame_counts(self):
        for duration in (16.08, 20.2, 60.08, 120.12):
            stored = struct.unpack('f', struct.pack('f', duration))[0]
            with self.subTest(duration=duration):
                self.assertEqual(video.frame_count(stored, 7.0, 25), round((duration + 7) * 25))
        stored = struct.unpack('f', struct.pack('f', 20.21))[0]
        with self.assertRaises(ValueError):
            video.frame_count(stored, 7.0, 25)

    def test_publication_never_replaces_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = (Path(directory) / name for name in ('source.mp4', 'target.mp4'))
            source.write_bytes(b'complete video')
            video._publish(source, target)
            self.assertEqual(target.read_bytes(), b'complete video')
            with self.assertRaises(FileExistsError):
                video._publish(source, target)
            self.assertEqual(target.read_bytes(), b'complete video')


if __name__ == '__main__':
    unittest.main()
