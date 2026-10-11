"""Texture checks solver feature provenance without using it for sampling."""
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ALG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ALG))
from atlas import Atlas
from input_adapter import InputSequence
from model import Rotor
from texture import validate_input_provenance, run_pass
from test_input_recon import bundle


class GeometrySidecarTextureProvenance(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        manifest = bundle(root, frames=(7, 19, 42, 99))
        original = InputSequence(root)
        access = copy.deepcopy(original.access_log)
        hashes = {a['path']: a['sha256'] for a in access if a['role'] == 'RGB'}
        observations = [{'frame': f['frame'], 't': f['t'], 'camera': 'A', 'uv': [7., 5.],
                         'rgb_sha256': hashes[f['cameras'][0]['rgb']], 'status': 'fixture',
                         'quality': {}, 'accept': False}
                        for f in manifest['frames']]
        anchor = {k: observations[0][k] for k in ('frame','t','camera','uv','rgb_sha256')}
        payload = {'schema': 'blade-texture-geometry.v1',
                   'pixel_coordinates': 'original_image_pixel_centers',
                   'provenance': {'role': 'solver_rgb_observations', 'method': 'declared RGB unit fixture',
                                  'allowed_inputs': ['declared_rgb','derived_mask','camera_calibration','frame_t']},
                   'tracks': [{'id': 'A-fixed', 'anchor': anchor, 'observations': observations}]}
        path = root/'observations/tracks.json'; path.parent.mkdir(); path.write_text(json.dumps(payload))
        sidecar = {'path': str(path), 'role': 'solver_rgb_observations',
                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
        result = {'input_access_log': access + [sidecar]}
        return root, manifest, payload, path, result

    def changed_payload(self, path, payload, result):
        path.write_text(json.dumps(payload))
        changed = copy.deepcopy(result)
        changed['input_access_log'][-1].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                              bytes=path.stat().st_size)
        return changed

    def test_full_feature_sidecar_checked_and_recorded_only_as_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest, payload, path, result = self.fixture(tmp)
            source = InputSequence(root)
            checked = validate_input_provenance(result, source, [{'frame': f['frame']} for f in manifest['frames']])
            self.assertEqual(len(checked), 1)
            self.assertEqual(checked[0]['role'], 'solver_rgb_observations')
            self.assertEqual(checked[0]['planned_observations'], 4)
            self.assertFalse(checked[0]['used_for_texture_sampling'])
            self.assertFalse(checked[0]['used_for_surface_binding'])

    def test_sparse_texture_sidecar_metadata_keeps_full_manifest_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest, payload, path, result = self.fixture(tmp)
            # These unselected bytes must not be opened just because the
            # original feature sidecar includes their declared metadata.
            (root/'A/000019.png').write_bytes(b'not an image')
            (root/'A/000042.png').write_bytes(b'not an image')
            source = InputSequence(root, selected_frames=[7, 99])
            checked = validate_input_provenance(result, source, [{'frame': 7}, {'frame': 99}])
            self.assertEqual(checked[0]['planned_observations'], 4)
            self.assertFalse(checked[0]['unselected_RGB_file_bytes_checked'])
            self.assertNotIn('A/000019.png', [a['path'] for a in source.access_log])

    def test_changed_sidecar_and_missing_hash_are_rejected(self):
        for mode in ('changed_bytes', 'missing_hash', 'wrong_bytes'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root, manifest, payload, path, result = self.fixture(tmp)
                if mode == 'changed_bytes':
                    path.write_bytes(path.read_bytes()+b' ')
                elif mode == 'missing_hash':
                    result['input_access_log'][-1].pop('sha256')
                else:
                    result['input_access_log'][-1]['bytes'] += 1
                with self.assertRaisesRegex(ValueError, 'provenance'):
                    validate_input_provenance(result, InputSequence(root), [{'frame': 7}])

    def test_forged_schema_roles_and_forbidden_answers_cannot_bypass_checker(self):
        for mode in ('schema','role','inputs','answer','log_role'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root, manifest, payload, path, result = self.fixture(tmp)
                if mode == 'schema': payload['schema'] = 'display-tracks.v1'
                elif mode == 'role': payload['provenance']['role'] = 'evaluation_only'
                elif mode == 'inputs': payload['provenance']['allowed_inputs'] = ['source_texture']
                elif mode == 'answer': payload['source_state'] = [0.]*13
                result = self.changed_payload(path, payload, result)
                if mode == 'log_role': result['input_access_log'][-1]['role'] = 'any_extra_role'
                with self.assertRaises(ValueError):
                    validate_input_provenance(result, InputSequence(root), [{'frame': 7}])

    def test_undeclared_frame_time_camera_rgb_and_pixel_contract_rejected(self):
        for mode in ('frame','time','camera','rgb','pixels'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root, manifest, payload, path, result = self.fixture(tmp)
                item = payload['tracks'][0]['observations'][2]
                if mode == 'frame': item['frame'] = 43
                elif mode == 'time': item['t'] += .01
                elif mode == 'camera': item['camera'] = 'HeldoutA'
                elif mode == 'rgb': item['rgb_sha256'] = '0'*64
                else: payload['pixel_coordinates'] = 'resized_pixel_centers'
                result = self.changed_payload(path, payload, result)
                with self.assertRaises(ValueError):
                    validate_input_provenance(result, InputSequence(root, selected_frames=[7,99]), [{'frame': 7}])

    def test_truth_heldout_and_symlink_paths_rejected_before_reading_sidecar(self):
        for forbidden in ('truth-input', 'locked-holdout', 'evaluation'):
            with self.subTest(forbidden=forbidden), tempfile.TemporaryDirectory() as tmp:
                root, manifest, payload, path, result = self.fixture(tmp)
                target = root/forbidden/'tracks.json'; target.parent.mkdir(); target.write_bytes(path.read_bytes())
                path.unlink(); path.symlink_to(target)
                with self.assertRaisesRegex(ValueError, 'forbidden solver sidecar'):
                    validate_input_provenance(result, InputSequence(root), [{'frame': 7}])

    def test_existing_rgb_mask_unknown_and_health_hash_checks_remain_strict(self):
        for role in ('RGB','RGB_derived_mask','human_2D_annotations','healthy_template'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as tmp:
                root, manifest, payload, path, result = self.fixture(tmp)
                source = InputSequence(root)
                existing = next((a for a in result['input_access_log'] if a['role'] == role), None)
                if existing is None:
                    existing = {'path': role+'.json', 'role': role, 'sha256': 'a'*64}
                    source.access_log.append(existing.copy())
                    result['input_access_log'].insert(0, existing.copy())
                    existing = result['input_access_log'][0]
                existing['sha256'] = 'changed'
                with self.assertRaisesRegex(ValueError, 'file hash mismatch'):
                    validate_input_provenance(result, source, [{'frame': 7}])

    def test_texture_never_rebinds_or_samples_feature_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest, payload, path, result = self.fixture(tmp)
            source = InputSequence(root, selected_frames=[7,99])
            rotor = Rotor(source.cfg); atlas = Atlas(rotor.tpl, dr=1., n_cols=8)
            result['frames'] = [{'frame': f, 't': f/10., 'state': np.zeros(13).tolist(),
                                 'observed_sections': np.zeros((3,8),int).tolist()} for f in (7,99)]
            args = SimpleNamespace(data=root,masks=None,max_frames=0,exposure_ms=0.,skip=0,fusion='best')
            with (patch('recon.TextureGeometry.prepare', side_effect=AssertionError('feature residual used')),
                  patch('recon.TextureGeometry.bind_anchors', side_effect=AssertionError('feature rebound'))):
                with redirect_stdout(io.StringIO()):
                    acc, _ = run_pass(args, rotor, atlas, result, sequence=source)
            self.assertFalse(acc.result()[2].any())
            self.assertEqual(acc.input_contract['solver_sidecar_provenance'][0]['planned_observations'], 4)


if __name__ == '__main__':
    unittest.main()
