"""Release summaries preserve independent installation identities and surrounding prose."""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/ci/check_release_identity.py'


@pytest.fixture
def identity():
    assert SCRIPT.is_file(), 'The release identity check must be available to CI'
    spec = importlib.util.spec_from_file_location('release_identity_check', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def records(tmp_path):
    publication = tmp_path / 'publication'
    publication.mkdir()
    release = publication / 'release.json'
    release.write_text(json.dumps(dict(
        schema='wfrl.extension-release.v1', version='1.2.3',
        archive=dict(name='wfrl_blender-1.2.3.zip', file_count=20,
                     sha256='a' * 64, download_url='https://example.org/addon.zip'),
    )))
    installed = publication / 'daily-installation.json'
    installed.write_text(json.dumps(dict(
        schema='wfrl.daily-installation.v1', version='1.2.3',
        installed_files_checked=20, loaded_module_files_checked=9,
        archive_sha256='a' * 64, same_as_public_archive=True, status='PASS', file_mismatches=0,
    )))
    document = tmp_path / 'README.md'
    document.write_text('before\n<!-- release-identity:start -->\nstale\n'
                        '<!-- release-identity:end -->\nafter\n')
    return tmp_path, release, installed, document


def test_check_rejects_stale_summary_without_writing(identity, records):
    root, release, installed, document = records
    before = document.read_bytes()
    with pytest.raises(ValueError, match='摘要'):
        identity.synchronize(root, release, installed, document)
    assert document.read_bytes() == before


def test_write_and_check_share_one_summary_and_preserve_prose(identity, records):
    root, release, installed, document = records
    assert identity.synchronize(root, release, installed, document, write=True)
    contents = document.read_text()
    assert contents.startswith('before\n') and contents.endswith('\nafter\n')
    assert '**1.2.3**' in contents and '20 个文件' in contents
    assert '(publication/release.json)' in contents
    assert not identity.synchronize(root, release, installed, document)


def test_equal_payload_claim_requires_matching_hash_and_count(identity, records):
    root, release, installed, document = records
    original = json.loads(installed.read_text())
    before = document.read_bytes()
    for field, value in [('archive_sha256', 'b' * 64), ('installed_files_checked', 19),
                         ('version', '1.2.2'), ('status', 'FAIL'), ('file_mismatches', 1)]:
        installed.write_text(json.dumps({**original, field: value}))
        with pytest.raises(ValueError, match='同一载荷'):
            identity.synchronize(root, release, installed, document, write=True)
        assert document.read_bytes() == before


def test_distinct_installation_is_rendered_without_claiming_release_equivalence(identity, records):
    root, release, installed, document = records
    data = json.loads(installed.read_text())
    data.update(version='1.2.2', archive_sha256='b' * 64, same_as_public_archive=False)
    installed.write_text(json.dumps(data))
    document = root / 'docs' / 'identity.md'
    document.parent.mkdir()
    document.write_text('<!-- release-identity:start -->\n<!-- release-identity:end -->\n')
    identity.synchronize(root, release, installed, document, write=True)
    contents = document.read_text()
    assert '**1.2.2**' in contents and '**1.2.3**' in contents
    assert '独立载荷' in contents and '(../publication/release.json)' in contents
    assert '同一载荷' not in contents


@pytest.mark.parametrize('body', ['', '<!-- release-identity:start -->\n',
                                 '<!-- release-identity:end -->\n<!-- release-identity:start -->',
                                 '<!-- release-identity:start -->\n<!-- release-identity:start -->\n'
                                 '<!-- release-identity:end -->'])
def test_ambiguous_markers_never_replace_document(identity, records, body):
    root, release, installed, document = records
    document.write_text(body)
    with pytest.raises(ValueError, match='标记'):
        identity.synchronize(root, release, installed, document, write=True)
    assert document.read_text() == body
