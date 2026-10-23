"""Publication filesystem boundaries using only non-media placeholder bytes."""
import errno
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from wfrl.camera_video import publication


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='manual-publication-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.fixture'
        self.source.write_bytes(b'NON-MEDIA complete placeholder')
        self.target = self.root / 'destination.fixture'

    def test_unsupported_hardlinks_do_not_prevent_complete_publication(self):
        with patch.object(os, 'link', side_effect=OSError(errno.ENOTSUP, 'no hard links')):
            publication.preflight(self.target)
            publication.publish(self.source, self.target)
        self.assertEqual(self.source.read_bytes(), self.target.read_bytes())
        self.assertFalse(list(self.root.glob('.wfrl-*')))

    def test_cross_filesystem_copy_is_staged_on_destination_filesystem(self):
        other = self.root / 'other-filesystem'
        other.mkdir()
        target = other / 'target.fixture'
        real_commit = publication.commit_exclusive
        def commit(source, destination):
            self.assertEqual(source.parent, destination.parent)
            real_commit(source, destination)
        with patch.object(os, 'link', side_effect=OSError(errno.EXDEV, 'cross device link')), \
             patch.object(publication, 'commit_exclusive', side_effect=commit):
            publication.publish(self.source, target)
        self.assertEqual(target.read_bytes(), self.source.read_bytes())

    def test_hardlink_commit_fallback_preserves_collision_protection(self):
        with patch.object(publication.ctypes, 'CDLL', return_value=object()):
            publication.preflight(self.target)
            publication.publish(self.source, self.target)
            with self.assertRaises(FileExistsError):
                publication.publish(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())

    def test_no_native_commit_and_no_hardlinks_are_rejected_before_rendering(self):
        with patch.object(publication.ctypes, 'CDLL', return_value=object()), \
             patch.object(os, 'link', side_effect=OSError(errno.ENOTSUP, 'unsupported hard links')):
            with self.assertRaises(OSError):
                publication.preflight(self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.root.glob('.wfrl-*')))

    def test_existing_file_and_dangling_symbolic_link_are_never_replaced(self):
        self.target.write_bytes(b'foreign data')
        for function in (publication.preflight, lambda target: publication.publish(self.source, target)):
            with self.assertRaises(FileExistsError):
                function(self.target)
        self.assertEqual(self.target.read_bytes(), b'foreign data')
        self.target.unlink()
        self.target.symlink_to(self.root / 'absent')
        with self.assertRaises(FileExistsError):
            publication.publish(self.source, self.target)
        self.assertTrue(self.target.is_symlink())

    def test_concurrent_publications_have_one_winner_and_no_partial_output(self):
        barrier = threading.Barrier(2)
        results = []
        def before(staging, identity):
            self.assertFalse(self.target.exists())
            self.assertEqual(staging.read_bytes(), self.source.read_bytes())
            barrier.wait(timeout=5)
        def run():
            try:
                publication.publish(self.source, self.target, before_commit=before)
                results.append('success')
            except FileExistsError:
                results.append('occupied')
        workers = [threading.Thread(target=run) for unused in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=8)
        self.assertEqual(sorted(results), ['occupied', 'success'])
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertFalse(list(self.root.glob('.wfrl-*')))

    def test_unsupported_commit_is_detected_by_preflight(self):
        with patch.object(publication, 'commit_exclusive', side_effect=OSError(errno.ENOTSUP, 'unsupported')):
            with self.assertRaises(OSError):
                publication.preflight(self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.root.glob('.wfrl-*')))

    def test_copy_and_commit_failures_leave_no_staging_or_partial_destination(self):
        for name in ('copyfileobj', 'commit'):
            target = publication.shutil if name == 'copyfileobj' else publication
            attribute = 'copyfileobj' if name == 'copyfileobj' else 'commit_exclusive'
            with self.subTest(name=name), patch.object(target, attribute, side_effect=OSError('simulated disk failure')):
                with self.assertRaises(OSError):
                    publication.publish(self.source, self.target)
            self.assertFalse(self.target.exists())
            self.assertFalse(list(self.root.glob('.wfrl-*')))
            self.assertEqual(self.source.read_bytes(), b'NON-MEDIA complete placeholder')

    def test_cleanup_does_not_remove_a_foreign_replacement_of_the_staging_name(self):
        commit = publication.commit_exclusive
        replaced = []
        def race(source, target):
            commit(source, target)
            source.write_bytes(b'foreign replacement')
            replaced.append(source)
        with patch.object(publication, 'commit_exclusive', side_effect=race):
            publication.publish(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertEqual(replaced[0].read_bytes(), b'foreign replacement')

    def test_late_foreign_file_and_dangling_link_win_the_name(self):
        for symlink in (False, True):
            def before(staging, identity):
                if symlink:
                    self.target.symlink_to(self.root / 'absent')
                else:
                    self.target.write_bytes(b'foreign concurrent owner')
            with self.subTest(symlink=symlink), self.assertRaises(FileExistsError):
                publication.publish(self.source, self.target, before_commit=before)
            self.assertEqual(self.target.is_symlink(), symlink)
            if not symlink:
                self.assertEqual(self.target.read_bytes(), b'foreign concurrent owner')
            self.target.unlink()
        self.assertFalse(list(self.root.glob('.wfrl-*')))


if __name__ == '__main__':
    unittest.main()
