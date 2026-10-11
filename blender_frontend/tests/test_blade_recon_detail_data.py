"""Content-bound provenance and matching geometry for the surface sidebar output."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np
import pytest

from wfrl_blender.blade_recon_data import (
    EXTERNAL_SOURCE_LABEL, MODEL_SHA256, ReconSequence, TexturePackage,
    detail_recon_path, detail_texture_path,
)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def display_uv(rotor):
    """Same float32 coordinate contract used by the Blender adapter."""
    tpl, N, S = rotor.tpl, rotor.cfg.n_ring, rotor.cfg.n_sections
    points = tpl.foils * tpl.chord[:, None, None]
    closed = np.concatenate([points, points[:, :1]], axis=1)
    cumulative = np.concatenate([np.zeros((S, 1)),
        np.cumsum(np.linalg.norm(np.diff(closed, axis=1), axis=-1), axis=1)], axis=1)
    tau = cumulative[:, :N] / cumulative[:, -1, None]
    v = 1 - (tpl.r - tpl.r[0]) / (tpl.r[-1] - tpl.r[0])
    faces = tpl.faces()
    u = tau.reshape(-1)[faces]
    rows = np.repeat(v[:, None], N, axis=1).reshape(-1)[faces]
    j = faces % N
    side = (np.arange(len(faces)) < (S - 1) * N * 2)[:, None]
    seam = (j == N - 1).any(axis=1, keepdims=True) & (j == 0)
    u = np.where(seam & side, 1., u)
    u = np.where(side, u, u[:, :1])
    return np.stack([u, rows], axis=-1).astype(np.float32)


def test_detail_assets_have_exact_source_hashes_and_independent_clock():
    root = detail_recon_path().parent
    manifest = json.loads((root / 'manifest.json').read_text())
    for name, record in manifest['files'].items():
        content = (root / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == record['sha256']
        assert len(content) == record['size_bytes']
        original = Path(record['source_path'])
        if original.is_file():
            assert content == original.read_bytes()
    sequence = ReconSequence(detail_recon_path())
    assert sequence.source_label.startswith('SYNTHETIC')
    assert len(sequence.frames) == 120
    assert sequence.fps == 10
    np.testing.assert_array_equal(sequence.source_frames, np.arange(120))
    saved = json.loads(detail_recon_path().read_text())
    np.testing.assert_array_equal(sequence.times, [frame['t'] for frame in saved['frames']])
    np.testing.assert_array_equal(np.round(sequence.times * 10).astype(int), sequence.source_frames)
    assert manifest['clock']['relationship_to_mappo'].startswith('independent')
    texture = TexturePackage(detail_texture_path(), sequence.rotor)
    assert texture.manifest['model_sha256'] == MODEL_SHA256
    assert texture.manifest['recon_sha256'] == sequence.source_sha256
    assert texture.manifest['clock']['time_end_s'] == 11.9
    assert [p['label'] for p in texture.manifest['inspection_points']] == ['亮斑', '横细线', '纵细线']


def test_all_saved_states_and_uv_match_the_preserved_v02_source_model():
    root = detail_recon_path().parent
    reference = load_module('wfrl_synth_tex_reference_model_test', root / 'reference/model.py')
    atlas = load_module('wfrl_synth_tex_reference_atlas_test', root / 'reference/atlas.py')
    try:
        sequence = ReconSequence(detail_recon_path())
        rotor = reference.Rotor(reference.TurbineConfig(**sequence.data['turbine']))
        assert 'healthy_template' not in sequence.data['turbine']
        np.testing.assert_array_equal(sequence.rotor.tpl.faces(), rotor.tpl.faces())
        for i, state in enumerate(sequence.states):
            V, A = sequence.geometry(i)
            expected_V, expected_A = rotor.forward(state)
            np.testing.assert_array_equal(V, expected_V)
            np.testing.assert_array_equal(A, expected_A)
        expected_uv = atlas.Atlas(rotor.tpl).corner_uv(rotor.tpl.faces()).astype(np.float32)
        np.testing.assert_array_equal(display_uv(sequence.rotor), expected_uv)
    finally:
        sys.modules.pop(reference.__name__, None)
        sys.modules.pop(atlas.__name__, None)


def test_relocated_recon_is_identified_by_content_and_changed_bytes_are_external():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'arbitrary-name.json'
        shutil.copyfile(detail_recon_path(), path)
        sequence = ReconSequence(path)
        assert sequence.source_label.startswith('SYNTHETIC')
        assert sequence.manifest is not None
        path.write_bytes(path.read_bytes() + b'\n')
        changed = ReconSequence(path)
        assert changed.source_label == EXTERNAL_SOURCE_LABEL
        assert changed.manifest is None


def test_changed_atlas_cannot_inherit_the_delivered_texture_provenance():
    sequence = ReconSequence(detail_recon_path())
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'texture'
        shutil.copytree(detail_texture_path(), path)
        assert TexturePackage(path, sequence.rotor).manifest['source_classification'] == 'SYNTHETIC'
        # Trailing bytes leave the PNG dimensions unchanged but change identity.
        image = path / 'blade2_tex.png'
        image.write_bytes(image.read_bytes() + b'changed')
        assert 'source_classification' not in TexturePackage(path, sequence.rotor).manifest


def test_builder_rejects_corrupted_surface_payload_and_clock():
    root = Path(__file__).resolve().parents[2]
    builder = load_module('wfrl_synth_tex_builder_test', root / 'scripts/blender/build_extension.py')
    try:
        prefix = 'assets/blade_recon_synth_tex/'
        payloads = [(prefix + p.relative_to(detail_recon_path().parent).as_posix(), p.read_bytes())
                    for p in detail_recon_path().parent.rglob('*') if p.is_file()]
        model_path = root / 'blender_frontend/wfrl_blender/_vendor/blade_recon/model.py'
        payloads.append(('_vendor/blade_recon/model.py', model_path.read_bytes()))
        builder._validate_blade_texture_payloads(payloads)
        damaged = dict(payloads)
        damaged[prefix + 'texture/blade1_tex.png'] += b'changed'
        with pytest.raises(ValueError, match='integrity mismatch'):
            builder._validate_blade_texture_payloads(list(damaged.items()))
        damaged = dict(payloads)
        metadata = json.loads(damaged[prefix + 'manifest.json'])
        metadata['clock']['samples'] = 601
        damaged[prefix + 'manifest.json'] = json.dumps(metadata).encode()
        with pytest.raises(ValueError, match='clock'):
            builder._validate_blade_texture_payloads(list(damaged.items()))
    finally:
        sys.modules.pop(builder.__name__, None)
