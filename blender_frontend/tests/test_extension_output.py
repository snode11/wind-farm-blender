"""Extension output conflicts, staging, and publication failures."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / 'scripts' / 'blender' / 'build_extension.py'


def load_builder():
    spec = importlib.util.spec_from_file_location('wfrl_output_builder', BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def output_paths(builder, directory):
    manifest = tomllib.loads((builder.PACKAGE_SOURCE / 'blender_manifest.toml').read_text())
    archive = directory / f"{manifest['id']}-{manifest['version']}.zip"
    return archive, archive.with_suffix('.zip.sha256'), archive.with_suffix('.inventory.json')


class OutputTests(unittest.TestCase):
    def test_each_existing_output_is_rejected_before_payload_reads(self):
        builder = load_builder()
        for index in range(3):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                paths = output_paths(builder, output)
                paths[index].write_bytes(b'existing output')
                with patch.object(builder, '_payloads', side_effect=AssertionError('payload read')):
                    with self.assertRaises(FileExistsError):
                        builder.build(output)
                self.assertEqual(list(output.iterdir()), [paths[index]])
                self.assertEqual(paths[index].read_bytes(), b'existing output')

    def test_complete_output_set_is_rejected_without_changes(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            for path in paths:
                path.write_bytes(path.name.encode())
            with patch.object(builder, '_payloads', side_effect=AssertionError('payload read')):
                with self.assertRaises(FileExistsError):
                    builder.build(output)
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()},
                             {path.name: path.name.encode() for path in paths})

    def test_broken_output_symlinks_are_conflicts(self):
        builder = load_builder()
        for index in range(3):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                paths = output_paths(builder, output)
                paths[index].symlink_to('missing-target')
                with patch.object(builder, '_payloads', side_effect=AssertionError('payload read')):
                    with self.assertRaises(FileExistsError):
                        builder.build(output)
                self.assertTrue(paths[index].is_symlink())
                self.assertFalse(paths[index].exists())
                self.assertEqual(list(output.iterdir()), [paths[index]])

    def test_explicit_overwrite_publishes_a_valid_package(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            for path in paths:
                path.write_bytes(b'old output')
            result = builder.build(output, overwrite=True)
            self.assertEqual(result.sha256, hashlib.sha256(result.archive.read_bytes()).hexdigest())
            self.assertEqual(result.checksum.read_text(), f'{result.sha256}  {result.archive.name}\n')
            inventory = json.loads(result.inventory.read_text())
            self.assertEqual(inventory['archive_sha256'], result.sha256)
            with ZipFile(result.archive) as archive:
                self.assertEqual(inventory['files'], [
                    dict(path=name, size=len(archive.read(name)),
                         sha256=hashlib.sha256(archive.read(name)).hexdigest())
                    for name in archive.namelist()
                ])
            self.assertEqual(set(output.iterdir()), set(paths))

    def test_overwrite_rejects_an_output_directory_before_payload_reads(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            paths[0].mkdir()
            with patch.object(builder, '_payloads', side_effect=AssertionError('payload read')):
                with self.assertRaises(IsADirectoryError):
                    builder.build(output, overwrite=True)
            self.assertTrue(paths[0].is_dir())

    def test_overwrite_replaces_output_symlinks_without_writing_their_targets(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            target = output / 'unrelated-file'
            target.write_bytes(b'keep target')
            staging = output / '.staging'
            staging.mkdir()
            for path in paths:
                path.symlink_to(target)
                (staging / path.name).write_bytes(b'new output')
            builder._publish_outputs(staging, paths, overwrite=True)
            self.assertEqual(target.read_bytes(), b'keep target')
            for path in paths:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b'new output')

    def test_validation_failure_preserves_existing_outputs(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            for path in paths:
                path.write_bytes(path.name.encode())
            with patch.object(builder, '_validate_farm_payloads', side_effect=ValueError('invalid payload')):
                with self.assertRaisesRegex(ValueError, 'invalid payload'):
                    builder.build(output, overwrite=True)
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()},
                             {path.name: path.name.encode() for path in paths})

    def test_sidecar_generation_failure_preserves_existing_outputs(self):
        self.check_generation_failure(overwrite=True)

    def test_sidecar_generation_failure_leaves_no_final_outputs(self):
        self.check_generation_failure(overwrite=False)

    def test_publication_failure_restores_existing_output_set(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            staging = output / '.staging'
            staging.mkdir()
            for path in paths:
                path.write_bytes(path.name.encode())
                (staging / path.name).write_bytes(b'new output')
            original_replace = os.replace

            def fail_archive(source, target):
                if Path(source).parent == staging and Path(target) == paths[0]:
                    raise OSError('archive publication failed')
                return original_replace(source, target)

            with patch.object(builder.os, 'replace', fail_archive):
                with self.assertRaisesRegex(OSError, 'archive publication failed'):
                    builder._publish_outputs(staging, paths, overwrite=True)
            for path in paths:
                self.assertEqual(path.read_bytes(), path.name.encode())

    def test_new_publication_failure_removes_published_sidecars(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            staging = output / '.staging'
            staging.mkdir()
            for path in paths:
                (staging / path.name).write_bytes(b'new output')
            original_link = os.link

            def fail_archive(source, target, **kwargs):
                if Path(target) == paths[0]:
                    raise OSError('archive publication failed')
                return original_link(source, target, **kwargs)

            with patch.object(builder.os, 'link', fail_archive):
                with self.assertRaisesRegex(OSError, 'archive publication failed'):
                    builder._publish_outputs(staging, paths, overwrite=False)
            self.assertFalse(any(path.exists() for path in paths))

    def test_default_publication_never_overwrites_a_late_conflict(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            staging = output / '.staging'
            staging.mkdir()
            for path in paths:
                (staging / path.name).write_bytes(b'new output')
            original_link = os.link

            def race_inventory(source, target, **kwargs):
                if Path(target) == paths[2]:
                    paths[2].write_bytes(b'concurrent output')
                return original_link(source, target, **kwargs)

            with patch.object(builder.os, 'link', race_inventory):
                with self.assertRaises(FileExistsError):
                    builder._publish_outputs(staging, paths, overwrite=False)
            self.assertEqual(paths[2].read_bytes(), b'concurrent output')
            self.assertFalse(paths[0].exists())
            self.assertFalse(paths[1].exists())

    def test_default_rollback_preserves_a_replacement_by_another_writer(self):
        self.check_rollback_conflict(overwrite=False)

    def test_overwrite_rollback_preserves_a_replacement_by_another_writer(self):
        self.check_rollback_conflict(overwrite=True)

    def check_rollback_conflict(self, *, overwrite):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            staging = output / '.staging'
            staging.mkdir()
            for path in paths:
                if overwrite:
                    path.write_bytes(path.name.encode())
                (staging / path.name).write_bytes(b'new output')
            original_replace = os.replace
            original_publish = os.replace if overwrite else os.link

            def replace_checksum_then_fail(source, target, **kwargs):
                if Path(source).parent == staging and Path(target) == paths[2]:
                    concurrent = output / '.concurrent-output'
                    concurrent.write_bytes(b'concurrent output')
                    original_replace(concurrent, paths[1])
                    raise OSError('inventory publication failed')
                return original_publish(source, target, **kwargs)

            with patch.object(builder.os, 'replace' if overwrite else 'link', replace_checksum_then_fail):
                with self.assertRaises((OSError, builder._PublicationRollbackError)) as caught:
                    builder._publish_outputs(staging, paths, overwrite=overwrite)
            self.assertTrue(paths[1].exists(), 'rollback deleted another writer\'s checksum')
            self.assertEqual(paths[1].read_bytes(), b'concurrent output')
            self.assertIsInstance(caught.exception, builder._PublicationRollbackError)
            self.assertIn('Recovery conflict', str(caught.exception))
            self.assertIn(str(staging), str(caught.exception))
            if overwrite:
                for path in paths:
                    self.assertEqual((staging / 'previous' / path.name).read_bytes(), path.name.encode())
                self.assertEqual(paths[0].read_bytes(), paths[0].name.encode())
                self.assertEqual(paths[2].read_bytes(), paths[2].name.encode())
            else:
                self.assertFalse(paths[0].exists())
                self.assertFalse(paths[2].exists())

    def check_generation_failure(self, *, overwrite):
        builder = load_builder()
        original_write = Path.write_text

        def fail_inventory(path, *args, **kwargs):
            if path.name.endswith('.inventory.json'):
                raise OSError('inventory write failed')
            return original_write(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = output_paths(builder, output)
            if overwrite:
                for path in paths:
                    path.write_bytes(path.name.encode())
            with patch.object(Path, 'write_text', fail_inventory):
                with self.assertRaisesRegex(OSError, 'inventory write failed'):
                    builder.build(output, **({'overwrite': True} if overwrite else {}))
            self.assertEqual(set(output.iterdir()), set(paths) if overwrite else set())
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()},
                             {path.name: path.name.encode() for path in paths} if overwrite else {})


if __name__ == '__main__':
    unittest.main()
