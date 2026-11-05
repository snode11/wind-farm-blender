"""Atomic JSON and text formatting, publication, cleanup and parent-directory contracts."""
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from wfrl_blender import atomic_json


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permission bits require a POSIX filesystem')
@pytest.mark.parametrize('mask,expected_mode', [(0o022, 0o644), (0o077, 0o600)],
                         ids=['umask-022', 'umask-077'])
@pytest.mark.parametrize('writer,value', [
    (atomic_json.write_json, {'label': '叶片'}),
    (atomic_json.write_text, '叶片姿态'),
], ids=['json', 'text'])
def test_new_file_permissions_follow_umask(tmp_path, writer, value, mask, expected_mode):
    target = tmp_path / 'status.json'
    previous_umask = os.umask(mask)
    try:
        writer(target, value)
        assert stat.S_IMODE(target.stat().st_mode) == expected_mode
        assert list(tmp_path.iterdir()) == [target]
    finally:
        os.umask(previous_umask)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permission bits require a POSIX filesystem')
@pytest.mark.parametrize('writer,value', [
    (atomic_json.write_json, {'label': '叶片'}),
    (atomic_json.write_text, '叶片姿态'),
], ids=['json', 'text'])
def test_overwrite_preserves_existing_file_permissions(tmp_path, writer, value):
    target = tmp_path / 'status.json'
    target.write_bytes(b'previous content')
    target.chmod(0o640)
    previous_umask = os.umask(0o077)
    try:
        writer(target, value)
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
        assert list(tmp_path.iterdir()) == [target]
    finally:
        os.umask(previous_umask)


@pytest.mark.parametrize('operation', [
    'fdopen',
    pytest.param('fchmod', marks=pytest.mark.skipif(
        not hasattr(os, 'fchmod'), reason='File-descriptor permissions are unavailable')),
])
def test_temporary_setup_failure_closes_descriptor_and_cleans_file(tmp_path, monkeypatch, operation):
    target = tmp_path / 'status.json'
    target.write_bytes(b'previous content')
    descriptors = []

    def fail(descriptor, *args, **kwargs):
        descriptors.append(descriptor)
        raise OSError('injected temporary setup failure')

    monkeypatch.setattr(atomic_json.os, operation, fail)
    with pytest.raises(OSError, match='injected temporary setup failure'):
        atomic_json.write_json(target, {'label': '叶片'})

    descriptor, = descriptors
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert target.read_bytes() == b'previous content'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('writer,value,expected', [
    (atomic_json.write_json, {'label': '叶片', 'value': 2}, '{\n  "label": "叶片",\n  "value": 2\n}\n'),
    (atomic_json.write_text, '叶片\r\n原样文本\n尾行', '叶片\r\n原样文本\n尾行'),
], ids=['json', 'text'])
def test_writer_flushes_and_syncs_before_atomic_replacement(tmp_path, monkeypatch, writer, value, expected):
    target = tmp_path / 'status.json'
    target.write_bytes(b'previous content')
    unrelated = tmp_path / 'status.json.tmp'
    unrelated.write_bytes(b'unrelated file')
    expected = expected.encode('utf-8')
    events = []
    fsync, replace = atomic_json.os.fsync, atomic_json.os.replace

    def synced(fd):
        temporary, = set(tmp_path.iterdir()) - {target, unrelated}
        assert temporary.read_bytes() == expected
        assert target.read_bytes() == b'previous content'
        fsync(fd)
        events.append('fsync')

    def replaced(source, destination):
        assert events == ['fsync']
        assert Path(source).parent == tmp_path
        assert Path(destination) == target
        replace(source, destination)
        events.append('replace')

    monkeypatch.setattr(atomic_json.os, 'fsync', synced)
    monkeypatch.setattr(atomic_json.os, 'replace', replaced)
    writer(str(target), value)

    assert target.read_bytes() == expected
    assert events == ['fsync', 'replace']
    assert unrelated.read_bytes() == b'unrelated file'
    assert set(tmp_path.iterdir()) == {target, unrelated}


@pytest.mark.parametrize('existing', [False, True])
def test_nan_rejection_cleans_temporary_file_and_preserves_destination(tmp_path, existing):
    target = tmp_path / 'status.json'
    if existing:
        target.write_bytes(b'previous content')

    with pytest.raises(ValueError):
        atomic_json.write_json(target, {'label': '叶片', 'value': float('nan')})

    assert set(tmp_path.iterdir()) == ({target} if existing else set())
    if existing:
        assert target.read_bytes() == b'previous content'


@pytest.mark.parametrize('operation', ['fsync', 'replace'])
@pytest.mark.parametrize('writer,value', [
    (atomic_json.write_json, {'value': 2}),
    (atomic_json.write_text, '叶片姿态'),
], ids=['json', 'text'])
def test_io_failure_cleans_temporary_file_and_preserves_destination(tmp_path, monkeypatch, operation, writer, value):
    target = tmp_path / 'status.json'
    target.write_bytes(b'previous content')

    def fail(*args):
        raise OSError('injected I/O failure')

    monkeypatch.setattr(atomic_json.os, operation, fail)
    with pytest.raises(OSError, match='injected I/O failure'):
        writer(target, value)

    assert target.read_bytes() == b'previous content'
    assert set(tmp_path.iterdir()) == {target}


@pytest.mark.parametrize('options', [{}, {'mkdir': False}])
@pytest.mark.parametrize('writer,value', [
    (atomic_json.write_json, {}),
    (atomic_json.write_text, ''),
], ids=['json', 'text'])
def test_writer_does_not_create_parents_by_default(tmp_path, options, writer, value):
    target = tmp_path / 'missing' / 'nested' / 'status.json'

    with pytest.raises(FileNotFoundError):
        writer(target, value, **options)

    assert list(tmp_path.iterdir()) == []


def test_mkdir_creates_parents_and_accepts_existing_directory(tmp_path):
    target = tmp_path / 'missing' / 'nested' / 'status.json'

    atomic_json.write_json(target, {'value': 1}, mkdir=True)
    atomic_json.write_json(target, {'value': 2}, mkdir=True)

    assert target.read_bytes() == b'{\n  "value": 2\n}\n'
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize('text', ['', '叶片', ' \t叶片\r\n尾行\r\n\n', '首行\r尾行', '{"姿态": 1}'])
def test_write_text_preserves_exact_utf8_bytes(tmp_path, text):
    target = tmp_path / 'poses.json'

    atomic_json.write_text(target, text)

    assert target.read_bytes() == text.encode('utf-8')
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('existing', [False, True])
def test_text_encoding_failure_cleans_temporary_file_and_preserves_destination(tmp_path, existing):
    target = tmp_path / 'poses.json'
    if existing:
        target.write_bytes(b'previous content')

    with pytest.raises(UnicodeEncodeError):
        atomic_json.write_text(target, 'unencodable surrogate: \ud800')

    assert set(tmp_path.iterdir()) == ({target} if existing else set())
    if existing:
        assert target.read_bytes() == b'previous content'


def test_write_text_mkdir_creates_parents_and_accepts_existing_directory(tmp_path):
    target = tmp_path / 'missing' / 'nested' / 'poses.json'

    atomic_json.write_text(target, 'first', mkdir=True)
    atomic_json.write_text(target, '叶片姿态', mkdir=True)

    assert target.read_bytes() == '叶片姿态'.encode('utf-8')
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize('module, name', [
    ('manual_video_job', 'write_json'),
    ('manual_video_render', '_write_json'),
])
def test_manual_video_file_entry_points_use_helper_without_blender(tmp_path, module, name):
    source = Path(atomic_json.__file__).with_name(module + '.py')
    target = tmp_path / 'status.json'
    code = '''import runpy, sys
namespace = runpy.run_path(sys.argv[1])
namespace[sys.argv[2]](sys.argv[3], {'label': '叶片'})
assert not {'bpy', 'numpy'} & sys.modules.keys()
'''
    subprocess.run([sys.executable, '-I', '-B', '-c', code, str(source), name, str(target)],
                   check=True, capture_output=True)

    assert target.read_bytes() == '{\n  "label": "叶片"\n}\n'.encode('utf-8')
    assert list(tmp_path.iterdir()) == [target]
