"""Reject same-source deliveries that rebind the source or alter sample timing."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'assets/blade_recon_mappo_tex/'


class SameSourcePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('same_source_builder', ROOT / 'scripts/blender/build_extension.py')
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)
        source = cls.builder.PACKAGE_SOURCE
        cls.payloads = {p.relative_to(source).as_posix(): p.read_bytes()
                        for folder in (source / PREFIX, source / 'assets/mappo')
                        for p in folder.rglob('*') if p.is_file()}
        cls.payloads['_vendor/blade_recon/model.py'] = (source / '_vendor/blade_recon/model.py').read_bytes()

    def validate(self, payloads):
        self.builder._validate_mappo_texture_payloads(list(payloads.items()))

    def changed_manifest(self, change):
        changed = dict(self.payloads)
        manifest = json.loads(changed[PREFIX + 'manifest.json'])
        change(manifest)
        changed[PREFIX + 'manifest.json'] = json.dumps(manifest).encode()
        return changed

    def changed_reconstruction(self, change):
        changed = dict(self.payloads)
        recon = json.loads(changed[PREFIX + 'recon.json'])
        change(recon)
        raw = json.dumps(recon).encode()
        changed[PREFIX + 'recon.json'] = raw
        manifest = json.loads(changed[PREFIX + 'manifest.json'])
        manifest['files']['recon.json'].update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        changed[PREFIX + 'manifest.json'] = json.dumps(manifest).encode()
        return changed

    def test_actual_delivery_matches_saved_source(self):
        self.validate(self.payloads)

    def test_texture_corruption_is_rejected(self):
        changed = dict(self.payloads)
        name = PREFIX + 'texture/blade1_tex.png'
        changed[name] = changed[name][:-1] + bytes([changed[name][-1] ^ 1])
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            self.validate(changed)

    def test_other_mappo_geometry_cannot_be_substituted(self):
        changed = dict(self.payloads)
        changed['assets/mappo/geometry.npz'] += b'changed source'
        with self.assertRaisesRegex(ValueError, 'another source package'):
            self.validate(changed)

    def test_rehashed_stretched_timeline_is_rejected(self):
        changed = dict(self.payloads)
        recon = json.loads(changed[PREFIX + 'recon.json'])
        recon['frames'][8]['t'] += .05
        raw = json.dumps(recon).encode()
        changed[PREFIX + 'recon.json'] = raw
        manifest = json.loads(changed[PREFIX + 'manifest.json'])
        manifest['files']['recon.json'].update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        changed[PREFIX + 'manifest.json'] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, '601 original'):
            self.validate(changed)

    def test_partial_model_equivalence_is_rejected(self):
        changed = dict(self.payloads)
        manifest = json.loads(changed[PREFIX + 'manifest.json'])
        manifest['model_compatibility']['models'][0]['samples_checked'] = 120
        changed[PREFIX + 'manifest.json'] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, 'equivalence is incomplete'):
            self.validate(changed)

    def test_model_identity_is_required_even_when_payload_hashes_match(self):
        for change in (lambda manifest: manifest.pop('model'),
                       lambda manifest: manifest['model'].pop('sha256'),
                       lambda manifest: manifest['model'].update(sha256='0'*64)):
            with self.subTest(change=change):
                with self.assertRaisesRegex(ValueError, 'model identity'):
                    self.validate(self.changed_manifest(change))

    def test_manifest_source_identity_matches_original_capture(self):
        with self.assertRaisesRegex(ValueError, 'another source package'):
            self.validate(self.changed_manifest(
                lambda manifest: manifest.update(mappo_manifest_sha256='0'*64)))
        for name in ('manifest.json', 'geometry.npz', 'blade-reference.json'):
            with self.subTest(missing_source=name):
                with self.assertRaisesRegex(ValueError, 'captured source identities'):
                    self.validate(self.changed_manifest(
                        lambda manifest, name=name: manifest['source_package_hashes'].pop('assets/mappo/' + name)))
        # Retain the documented explicit source-map fallback for older saved
        # provenance; no anonymous default is manufactured.
        self.validate(self.changed_manifest(lambda manifest: manifest.pop('mappo_manifest_sha256')))
        changed = dict(self.payloads)
        changed['assets/mappo/manifest.json'] += b'\n'
        manifest = json.loads(changed[PREFIX + 'manifest.json'])
        other = hashlib.sha256(changed['assets/mappo/manifest.json']).hexdigest()
        manifest['mappo_manifest_sha256'] = other
        manifest['source_package_hashes']['assets/mappo/manifest.json'] = other
        changed[PREFIX + 'manifest.json'] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, 'another source package'):
            self.validate(changed)

    def test_manifest_requires_complete_absolute_and_relative_clock(self):
        fields = ('samples', 'fps', 'source_frame_start', 'source_frame_end',
                  'time_start_s', 'time_end_s', 'simulation_start_s', 'simulation_end_s',
                  'source_hz', 'timeline_fps', 'stride', 'frame_start', 'frame_end')
        for field in fields:
            for kind in ('missing', 'wrong', 'string', 'boolean'):
                def change(manifest, field=field, kind=kind):
                    clock = manifest['clock']
                    if kind == 'missing':
                        clock.pop(field)
                    elif kind == 'wrong':
                        clock[field] += 1
                    elif kind == 'string':
                        clock[field] = str(clock[field])
                    else:
                        clock[field] = bool(clock[field])
                with self.subTest(field=field, kind=kind):
                    with self.assertRaisesRegex(ValueError, 'clock does not match'):
                        self.validate(self.changed_manifest(change))

    def test_rehashed_absolute_sample_clock_requires_saved_values(self):
        for field in ('sim_t', 'blender_frame'):
            for kind in ('missing', 'wrong', 'nonfinite', 'string', 'wrong_numeric_type'):
                def change(recon, field=field, kind=kind):
                    row = recon['frames'][13]
                    if kind == 'missing':
                        row.pop(field)
                    elif kind == 'wrong':
                        row[field] += 1
                    elif kind == 'nonfinite':
                        row[field] = float('nan')
                    elif kind == 'string':
                        row[field] = str(row[field])
                    elif field == 'blender_frame':
                        row[field] = float(row[field])
                    else:
                        row[field] = None
                with self.subTest(field=field, kind=kind):
                    with self.assertRaisesRegex(ValueError, '601 original'):
                        self.validate(self.changed_reconstruction(change))

    def test_rehashed_boolean_source_and_display_frame_ids_are_rejected(self):
        for field, value in (('frame', False), ('blender_frame', True)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, '601 original'):
                    self.validate(self.changed_reconstruction(
                        lambda recon, field=field, value=value: recon['frames'][0].update({field: value})))


if __name__ == '__main__':
    unittest.main()
