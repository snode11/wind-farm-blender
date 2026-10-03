"""Directory consolidation resolves frozen records without old aliases."""
import hashlib
from pathlib import Path

import pytest

from wfrl.nrel_reconstruction.artifact_paths import (
    MIGRATION_ORIGINALS, NREL_DIRECTORY, frozen_path, relocated_path, repository_root,
)


@pytest.fixture
def checkout(tmp_path):
    (tmp_path / '.git').mkdir()
    (tmp_path / 'wfrl').mkdir()
    return tmp_path


def test_repository_search_handles_nested_stage_scripts(checkout):
    script = checkout / NREL_DIRECTORY / 'stages/06-p5-observation/annotation/tool.py'
    script.parent.mkdir(parents=True)
    script.write_text('')
    assert repository_root(script) == checkout


@pytest.mark.parametrize('recorded', [
    'outputs/nrel-video-single-blade/20261002-second/observations/cache.png',
    '/old/checkout/outputs/nrel-video-single-blade/20261002-second/observations/cache.png',
    '20261002-second/observations/cache.png',
    'outputs/nrel-video-single-blade/stages/02-second-run/observations/cache.png',
])
def test_old_and_new_recorded_paths_share_actual_location(checkout, recorded):
    expected = checkout / NREL_DIRECTORY / 'stages/02-second-run/observations/cache.png'
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b'cache')
    assert relocated_path(recorded, root=checkout) == expected
    assert not (checkout / NREL_DIRECTORY / '20261002-second').exists()


def test_repository_relative_paths_do_not_depend_on_cwd(checkout, tmp_path, monkeypatch):
    other = tmp_path / 'elsewhere'; other.mkdir()
    monkeypatch.chdir(other)
    assert relocated_path('scripts/tool.py', root=checkout) == checkout / 'scripts/tool.py'
    assert relocated_path('outputs/nrel-video-single-blade/review.zip.sha256', root=checkout) == (
        checkout / NREL_DIRECTORY / 'review/review.zip.sha256')
    assert relocated_path('20261003-p3-center-scale-review.verification.json', root=checkout) == (
        checkout / NREL_DIRECTORY / 'review/20261003-p3-center-scale-review.verification.json')


def test_frozen_digest_uses_visible_original_and_rejects_unmatched_bytes(checkout):
    old = '20261003-p5-observation/annotation/build_annotation.py'
    current = relocated_path(old, root=checkout)
    original = checkout / NREL_DIRECTORY / MIGRATION_ORIGINALS / old
    current.parent.mkdir(parents=True); original.parent.mkdir(parents=True)
    current.write_bytes(b'updated import paths')
    original.write_bytes(b'historical script')
    signature = hashlib.sha256(original.read_bytes()).hexdigest()
    assert frozen_path(old, signature, root=checkout) == original
    assert frozen_path(current, signature, root=checkout) == original
    with pytest.raises(RuntimeError, match='No saved artifact matches'):
        frozen_path(old, '0' * 64, root=checkout)


def test_historical_source_signature_returns_snapshot_explicitly(checkout):
    current = checkout / 'wfrl/nrel_reconstruction/optimize.py'
    saved = checkout / NREL_DIRECTORY / 'stages/02-second-run/baseline-code/wfrl/nrel_reconstruction/optimize.py'
    current.parent.mkdir(parents=True); saved.parent.mkdir(parents=True)
    current.write_bytes(b'new code'); saved.write_bytes(b'historical code')
    signature = hashlib.sha256(saved.read_bytes()).hexdigest()
    assert frozen_path(current, signature, root=checkout) == saved


def test_frozen_repository_document_uses_signed_original(checkout):
    document = checkout / 'docs/proposal/plan.md'
    original = checkout / NREL_DIRECTORY / MIGRATION_ORIGINALS / 'repository/docs/proposal/plan.md'
    document.parent.mkdir(parents=True); original.parent.mkdir(parents=True)
    document.write_bytes(b'current links'); original.write_bytes(b'historical links')
    signature = hashlib.sha256(original.read_bytes()).hexdigest()
    assert frozen_path(document, signature, root=checkout) == original


@pytest.mark.parametrize('relative', [
    'wfrl/nrel_reconstruction/optimize.py',
    'scripts/nrel_single_blade.py',
    'tests/test_nrel_optimizer.py',
    'docs/proposal/plan.md',
])
def test_old_absolute_source_uses_new_checkout_snapshot(checkout, tmp_path, relative):
    old = checkout.parent / (checkout.name + '-old checkout') / relative
    current = checkout / relative
    saved = checkout / NREL_DIRECTORY / 'stages/02-second-run/baseline-code' / relative
    old.parent.mkdir(parents=True, exist_ok=True)
    current.parent.mkdir(parents=True, exist_ok=True)
    saved.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b'historical code')
    current.write_bytes(b'new code')
    saved.write_bytes(b'historical code')
    signature = hashlib.sha256(saved.read_bytes()).hexdigest()
    assert frozen_path(old, signature, root=checkout) == saved
    old.unlink()
    assert frozen_path(old, signature, root=checkout) == saved


def test_frozen_source_cannot_fall_back_to_old_working_directory(checkout, tmp_path):
    old = checkout.parent / (checkout.name + '-old checkout') / 'scripts/tool.py'
    current = checkout / 'scripts/tool.py'
    old.parent.mkdir(parents=True); current.parent.mkdir(parents=True)
    old.write_bytes(b'original source')
    current.write_bytes(b'changed source')
    signature = hashlib.sha256(old.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match='No saved artifact matches'):
        frozen_path(old, signature, root=checkout)
