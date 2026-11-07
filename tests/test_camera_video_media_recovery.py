"""Encoding ownership recovery with mocked codecs and non-media placeholders."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wfrl.camera_video import media


class EncodingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='manual-encoding-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'master_frames').mkdir()
        for index in range(3):
            (self.root / 'master_frames' / f'{index:06d}.png').write_bytes(f'NON-MEDIA {index}'.encode())
        with (self.root / 'frames.csv').open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerows([['frame_id', 'video_pts'], [0, 0], [1, 1], [2, 2]])
        self.calls = 0
        self.run_patch = patch.object(media, '_run', side_effect=self.mock_run)
        self.validate_patch = patch.object(media, 'validate_video', side_effect=self.mock_validate)
        self.run_patch.start()
        self.validate_patch.start()
        self.addCleanup(self.run_patch.stop)
        self.addCleanup(self.validate_patch.stop)

    def mock_run(self, args):
        if '-framerate' in args:
            self.calls += 1
            Path(args[-1]).write_bytes(b'NON-MEDIA owned encoded placeholder')
            return ''
        return 'fixture executable version\n'

    def mock_validate(self, path, *args, **kwargs):
        if Path(path).read_bytes() != b'NON-MEDIA owned encoded placeholder':
            raise ValueError('simulated corrupt video')
        return dict(valid=True, frame_count=3, width=1920, height=1080, video_sha256=media.sha256(path))

    def test_complete_owned_encoding_is_reused_only_with_explicit_resume(self):
        original = media.encode_video(self.root, 25)
        with self.assertRaises(FileExistsError):
            media.encode_video(self.root, 25)
        self.assertEqual(media.encode_video(self.root, 25, resume=True), original)
        self.assertEqual(self.calls, 1)

    def test_expected_master_identity_is_checked_before_starting_the_codec(self):
        expected = {path.name: media.sha256(path) for path in (self.root / 'master_frames').iterdir()}
        (self.root / 'master_frames/000001.png').write_bytes(b'NON-MEDIA changed frame')
        with self.assertRaises(ValueError):
            media.encode_video(self.root, 25, expected_master_sha256=expected)
        self.assertEqual(self.calls, 0)
        self.assertFalse((self.root / 'video.mp4').exists())

    def test_recovery_after_output_commit_before_final_report(self):
        original = media._write_json_atomic
        def interrupted(path, value):
            if path.name == 'encoding.json':
                raise OSError('simulated interruption')
            original(path, value)
        with patch.object(media, '_write_json_atomic', side_effect=interrupted):
            with self.assertRaises(OSError):
                media.encode_video(self.root, 25)
        self.assertTrue((self.root / 'video.mp4').exists())
        self.assertTrue(media.encode_video(self.root, 25, resume=True)['valid'])
        self.assertEqual(self.calls, 1)

    def test_changed_saved_dimensions_do_not_override_verified_video(self):
        media.encode_video(self.root, 25)
        path = self.root / 'checks/encoding.json'
        report = json.loads(path.read_text())
        report['width'] = 128
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'report differs'):
            media.encode_video(self.root, 25, resume=True)

    def test_owned_partial_encoding_can_be_rebuilt(self):
        def interrupted(args):
            Path(args[-1]).write_bytes(b'NON-MEDIA partial placeholder')
            raise OSError('simulated codec exit')
        with patch.object(media, '_run', side_effect=interrupted):
            with self.assertRaises(OSError):
                media.encode_video(self.root, 25)
        self.assertTrue(media.encode_video(self.root, 25, resume=True)['valid'])

    def test_altered_parameters_master_or_csv_refuse_complete_recovery(self):
        media.encode_video(self.root, 25)
        original = (self.root / 'video.mp4').read_bytes()
        with self.assertRaises(ValueError):
            media.encode_video(self.root, 25, resume=True, crf=19)
        frame = self.root / 'master_frames/000001.png'
        frame.write_bytes(b'other job')
        with self.assertRaises(ValueError):
            media.encode_video(self.root, 25, resume=True)
        self.assertEqual(original, (self.root / 'video.mp4').read_bytes())

    def test_unknown_output_temporary_and_symlink_are_preserved(self):
        for name in ('video.mp4', 'video.encoding.mp4'):
            path = self.root / name
            path.write_bytes(b'foreign owner')
            with self.subTest(name=name), self.assertRaises(FileExistsError):
                media.encode_video(self.root, 25, resume=True)
            self.assertEqual(path.read_bytes(), b'foreign owner')
            path.unlink()
        path = self.root / 'video.mp4'
        path.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            media.encode_video(self.root, 25, resume=True)
        self.assertTrue(path.is_symlink())

    def test_unknown_saved_report_is_preserved_before_encoding(self):
        (self.root / 'checks').mkdir()
        report = self.root / 'checks/encoding.json'
        report.write_text(json.dumps(dict(owner='foreign')))
        previous = report.read_bytes()
        with self.assertRaises(ValueError):
            media.encode_video(self.root, 25, resume=True)
        self.assertEqual(report.read_bytes(), previous)
        self.assertEqual(self.calls, 0)

    def test_concurrent_foreign_video_cannot_be_replaced_at_commit(self):
        original = self.mock_run
        def raced(args):
            result = original(args)
            if '-framerate' in args:
                (self.root / 'video.mp4').write_bytes(b'concurrent owner')
            return result
        with patch.object(media, '_run', side_effect=raced):
            with self.assertRaises(FileExistsError):
                media.encode_video(self.root, 25)
        self.assertEqual((self.root / 'video.mp4').read_bytes(), b'concurrent owner')


if __name__ == '__main__':
    unittest.main()
