"""Publishing and recovering manual Down video sets."""
import json
import os
from pathlib import Path
import stat
import sys

import pytest

from blender_frontend.tests._platform import symlink_or_skip
from wfrl_blender import atomic_json, video_output


def _movie(directory, name, data):
    path = Path(directory, name)
    path.write_bytes(data)
    return path


def test_publish_replaces_every_target_and_leaves_no_staged_files(tmp_path):
    render, out = tmp_path / 'render', tmp_path / 'out'
    render.mkdir()
    out.mkdir()
    (out / 'clip.mp4').write_bytes(b'old')
    movies = [(_movie(render, 'a.mp4', b'center'), out / 'clip.mp4'),
              (_movie(render, 'b.mp4', b'left'), out / 'clip_left.mp4')]
    video_output.publish_staged(movies, out / 'clip.json', {'frames': 3}, '三路视频')
    assert (out / 'clip.mp4').read_bytes() == b'center'
    assert (out / 'clip_left.mp4').read_bytes() == b'left'
    assert json.loads((out / 'clip.json').read_text(encoding='utf-8')) == {'frames': 3}
    assert sorted(p.name for p in out.iterdir()) == ['clip.json', 'clip.mp4', 'clip_left.mp4']
    assert not any(render.iterdir())


def test_preflight_allows_missing_parent_without_creating_it(tmp_path):
    out = tmp_path / 'out'
    video_output.validate_publish_targets([out / 'clip.mp4', out / 'clip.json'])
    assert not out.exists()


def test_preflight_rejects_a_target_that_is_also_an_output_directory(tmp_path):
    target = tmp_path / 'clip.mp4'
    with pytest.raises(ValueError):
        video_output.validate_publish_targets([target, target / 'clip.json'])
    assert not target.exists()


@pytest.mark.parametrize('member', ['movie', 'sidecar'])
@pytest.mark.parametrize('existing', [False, True])
def test_publish_rejects_changed_preflight_member(tmp_path, member, existing):
    movie = _movie(tmp_path, 'rendered.mp4', b'new movie')
    target, sidecar = tmp_path / 'clip.mp4', tmp_path / 'clip.json'
    target.write_bytes(b'approved movie')
    sidecar.write_bytes(b'approved sidecar')
    changed = target if member == 'movie' else sidecar
    other = sidecar if member == 'movie' else target
    other_contents = other.read_bytes()
    if not existing:
        changed.unlink()
    preflight = video_output.validate_publish_targets([target, sidecar])
    external = _movie(tmp_path, 'external', b'external')
    os.replace(external, changed)

    with pytest.raises(RuntimeError, match='视频输出目标'):
        video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频', preflight=preflight)

    assert movie.read_bytes() == b'new movie'
    assert changed.read_bytes() == b'external' and other.read_bytes() == other_contents
    assert not list(tmp_path.glob('.wfrl-down-*'))


@pytest.mark.skipif(os.name == 'nt', reason='POSIX metadata-only ctime changes')
@pytest.mark.parametrize('touch', ['hard_link', 'same_mode'])
def test_publish_accepts_metadata_only_change_after_preflight(tmp_path, touch):
    movie = _movie(tmp_path, 'rendered.mp4', b'new movie')
    target, sidecar = _movie(tmp_path, 'clip.mp4', b'approved movie'), tmp_path / 'clip.json'
    preflight = video_output.validate_publish_targets([target, sidecar])
    before = target.stat()
    if touch == 'hard_link':
        link = tmp_path / 'opened-elsewhere.mp4'
        os.link(target, link)
        link.unlink()
    else:
        os.chmod(target, stat.S_IMODE(before.st_mode))
    after = target.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)

    video_output.publish_staged([(movie, target)], sidecar, {'frames': 1}, 'Down 视频', preflight=preflight)

    assert target.read_bytes() == b'new movie'
    assert json.loads(sidecar.read_text(encoding='utf-8')) == {'frames': 1}
    assert sorted(path.name for path in tmp_path.iterdir()) == ['clip.json', 'clip.mp4']


def test_publish_rejects_target_rewritten_in_place_after_preflight(tmp_path):
    movie = _movie(tmp_path, 'rendered.mp4', b'new movie')
    target, sidecar = _movie(tmp_path, 'clip.mp4', b'approved movie'), tmp_path / 'clip.json'
    preflight = video_output.validate_publish_targets([target, sidecar])
    before = target.stat()
    target.write_bytes(b'external movie')
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    assert target.stat().st_ino == before.st_ino and target.stat().st_size == before.st_size

    with pytest.raises(RuntimeError, match='视频输出目标在预检后发生变化'):
        video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频', preflight=preflight)

    assert movie.read_bytes() == b'new movie'
    assert target.read_bytes() == b'external movie' and not sidecar.exists()
    assert not list(tmp_path.glob('.wfrl-down-*'))


@pytest.mark.parametrize('redirect', ['symlink', 'directory'])
def test_publish_rejects_parent_redirect_after_preflight(tmp_path, redirect):
    original, replacement, alias = (tmp_path / name for name in ('original', 'replacement', 'alias'))
    original.mkdir()
    if redirect == 'symlink':
        symlink_or_skip(alias, original, target_is_directory=True)
        replacement.mkdir()
    else:
        alias = original
    target, sidecar = alias / 'clip.mp4', alias / 'clip.json'
    movie = _movie(tmp_path, 'rendered.mp4', b'new movie')
    preflight = video_output.validate_publish_targets([target, sidecar])
    if redirect == 'symlink':
        alias.unlink()
        symlink_or_skip(alias, replacement, target_is_directory=True)
    else:
        original.rename(replacement)
        original.mkdir()

    with pytest.raises(RuntimeError, match='视频输出目标'):
        video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频', preflight=preflight)

    assert movie.read_bytes() == b'new movie'
    assert not list(original.iterdir()) and not list(replacement.iterdir())


def test_publish_rechecks_the_preflight_after_staging(tmp_path, monkeypatch):
    target = _movie(tmp_path, 'clip.mp4', b'approved movie')
    sidecar = tmp_path / 'clip.json'
    movie = _movie(tmp_path, 'rendered.mp4', b'new movie')
    preflight = video_output.validate_publish_targets([target, sidecar])
    move = video_output.shutil.move

    def move_with_competing_write(source, destination):
        result = move(source, destination)
        sidecar.write_bytes(b'external sidecar')
        return result

    monkeypatch.setattr(video_output.shutil, 'move', move_with_competing_write)
    with pytest.raises(RuntimeError, match='视频输出目标'):
        video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频', preflight=preflight)
    assert target.read_bytes() == b'approved movie' and sidecar.read_bytes() == b'external sidecar'
    assert not list(tmp_path.glob('.wfrl-down-*'))


def test_publish_rejects_directory_destination_before_moving_movie(tmp_path):
    target = tmp_path / 'clip.mp4'
    sidecar_target = tmp_path / 'clip.json'
    sidecar_target.mkdir()
    movie = _movie(tmp_path, 'rendered.mp4', b'new')
    with pytest.raises(ValueError):
        video_output.publish_staged([(movie, target)], sidecar_target, {}, 'Down 视频')
    assert movie.read_bytes() == b'new'
    assert not target.exists()
    assert list(sidecar_target.iterdir()) == []


def test_publish_leaves_unrelated_partial_file_untouched(tmp_path):
    target = tmp_path / 'clip.mp4'
    partial = _movie(tmp_path, '.clip.mp4.partial', b'unrelated')
    movie = _movie(tmp_path, 'rendered.mp4', b'new')
    video_output.publish_staged([(movie, target)], tmp_path / 'clip.json', {}, 'Down 视频')
    assert partial.read_bytes() == b'unrelated'
    assert target.read_bytes() == b'new'


@pytest.mark.parametrize('alias_name', [
    'take_left.mp4',
    pytest.param('TAKE_LEFT.MP4', marks=pytest.mark.skipif(
        sys.platform != 'darwin' and os.name != 'nt', reason='macOS and Windows case aliases')),
])
def test_publish_rejects_aliased_movie_targets_before_moving_files(tmp_path, alias_name):
    render, out = tmp_path / 'render', tmp_path / 'out'
    render.mkdir()
    out.mkdir()
    target = _movie(out, 'take_left.mp4', b'old')
    center = _movie(render, 'center.mp4', b'center')
    left = _movie(render, 'left.mp4', b'left')
    with pytest.raises(ValueError):
        video_output.publish_staged([(center, target), (left, out / alias_name)],
                                    out / 'take_left.json', {}, '三路视频')
    assert target.read_bytes() == b'old'
    assert center.read_bytes() == b'center'
    assert left.read_bytes() == b'left'
    assert list(out.iterdir()) == [target]


def test_publish_restores_existing_and_absent_targets_when_sidecar_commit_fails(tmp_path, monkeypatch):
    render, out = tmp_path / 'render', tmp_path / 'out'
    render.mkdir()
    out.mkdir()
    center_target = _movie(out, 'clip.mp4', b'old center')
    left_target = out / 'clip_left.mp4'
    sidecar_target = _movie(out, 'clip.json', b'{"old": true}\n')
    movies = [(_movie(render, 'center.mp4', b'new center'), center_target),
              (_movie(render, 'left.mp4', b'new left'), left_target)]
    commit = video_output._commit_export
    failed = False

    def fail_sidecar_commit(source, target):
        nonlocal failed
        if not failed and Path(target) == sidecar_target:
            failed = True
            assert center_target.read_bytes() == b'new center'
            assert left_target.read_bytes() == b'new left'
            raise PermissionError('Sidecar became locked')
        return commit(source, target)

    monkeypatch.setattr(video_output, '_commit_export', fail_sidecar_commit)
    with pytest.raises(RuntimeError, match='三路视频写出失败') as raised:
        video_output.publish_staged(movies, sidecar_target, {'frames': 3}, '三路视频')
    assert isinstance(raised.value.__cause__, PermissionError)
    assert center_target.read_bytes() == b'old center'
    assert sidecar_target.read_bytes() == b'{"old": true}\n'
    assert sorted(path.name for path in out.iterdir()) == ['clip.json', 'clip.mp4']


def test_rollback_retains_backup_if_another_writer_replaced_the_movie(tmp_path, monkeypatch):
    render, out = tmp_path / 'render', tmp_path / 'out'
    render.mkdir()
    out.mkdir()
    target = _movie(out, 'clip.mp4', b'old')
    sidecar = _movie(out, 'clip.json', b'{"old": true}\n')
    movie = _movie(render, 'rendered.mp4', b'new')
    external = _movie(out, 'external.mp4', b'external')
    commit = video_output._commit_export
    failed = False

    def fail_after_external_write(source, destination):
        nonlocal failed
        if not failed and Path(destination) == sidecar:
            failed = True
            os.replace(external, target)
            raise PermissionError('Sidecar became locked')
        return commit(source, destination)

    monkeypatch.setattr(video_output, '_commit_export', fail_after_external_write)
    with pytest.raises(RuntimeError, match='恢复失败') as raised:
        video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频')
    assert target.read_bytes() == b'external'
    backups = list(out.glob('*/*'))
    assert [backup.read_bytes() for backup in backups] == [b'old']
    assert str(backups[0]) in str(raised.value)


@pytest.mark.parametrize('entry_type', ['file', 'symlink', 'directory'])
def test_publish_preserves_entry_replaced_at_commit_boundary(tmp_path, monkeypatch, entry_type):
    target = _movie(tmp_path, 'clip.mp4', b'old')
    movie = _movie(tmp_path, 'rendered.mp4', b'new')
    external = tmp_path / 'external.mp4'
    if entry_type == 'file':
        external.write_bytes(b'external')
    elif entry_type == 'symlink':
        _movie(tmp_path, 'referent.mp4', b'external')
        symlink_or_skip(external, Path('referent.mp4'))
    else:
        external.mkdir()
        _movie(external, 'child.mp4', b'external')
    original_stat = external.lstat()
    sidecar = tmp_path / 'clip.json'
    replace = os.replace
    injected = False

    def replace_after_external_update(source, destination):
        nonlocal injected
        if not injected and target in (Path(source), Path(destination)):
            if external.is_dir():
                target.unlink()
            replace(external, target)
            injected = True
        return replace(source, destination)

    monkeypatch.setattr(os, 'replace', replace_after_external_update)
    with pytest.raises(RuntimeError):
        video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频')
    assert injected
    assert os.path.samestat(target.lstat(), original_stat)
    assert (target / 'child.mp4' if entry_type == 'directory' else target).read_bytes() == b'external'
    assert not sidecar.exists()
    assert not list(tmp_path.glob('.wfrl-down-*'))


@pytest.mark.parametrize(('second_writer', 'interrupted'), [
    (False, False),
    (True, False),
    (False, True),
])
def test_rollback_preserves_external_entries_at_displacement_boundary(tmp_path, monkeypatch, second_writer, interrupted):
    target = _movie(tmp_path, 'clip.mp4', b'old')
    sidecar = _movie(tmp_path, 'clip.json', b'old sidecar')
    movie = _movie(tmp_path, 'rendered.mp4', b'new')
    external = _movie(tmp_path, 'external.mp4', b'external')
    replace = os.replace
    commit = video_output._commit_export
    failed = injected = False

    def fail_sidecar_commit():
        nonlocal failed
        failed = True
        assert target.read_bytes() == b'new'
        raise PermissionError('Sidecar became locked')

    def replace_with_race(source, destination):
        nonlocal injected
        if failed and not injected and target in (Path(source), Path(destination)):
            replace(external, target)
            injected = True
            replace(source, destination)
            if interrupted:
                raise KeyboardInterrupt('Interrupted immediately after the rollback move')
            return
        return replace(source, destination)

    def commit_with_race(source, destination):
        if not failed and Path(destination) == sidecar:
            fail_sidecar_commit()
        if injected and second_writer and Path(destination) == target:
            target.write_bytes(b'latest')
        return commit(source, destination)

    monkeypatch.setattr(os, 'replace', replace_with_race)
    monkeypatch.setattr(video_output, '_commit_export', commit_with_race)
    with pytest.raises(RuntimeError) as raised:
        video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频')
    assert injected
    assert target.read_bytes() == (b'latest' if second_writer else b'external')
    assert sidecar.read_bytes() == b'old sidecar'
    backups = {path.read_bytes(): path for path in tmp_path.glob('.wfrl-down-*/*')}
    assert set(backups) == ({b'old', b'external'} if second_writer else {b'old'})
    assert all(str(path) in str(raised.value) for path in backups.values())


def test_publish_preserves_new_entry_created_after_displacement(tmp_path, monkeypatch):
    target = _movie(tmp_path, 'clip.mp4', b'old')
    movie = _movie(tmp_path, 'rendered.mp4', b'new')
    sidecar = tmp_path / 'clip.json'
    commit = video_output._commit_export
    injected = False

    def commit_with_race(source, destination):
        nonlocal injected
        if not injected and Path(destination) == target:
            with target.open('xb') as handle:
                handle.write(b'external')
            injected = True
        return commit(source, destination)

    monkeypatch.setattr(video_output, '_commit_export', commit_with_race)
    with pytest.raises(RuntimeError) as raised:
        video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频')
    assert injected
    assert target.read_bytes() == b'external'
    assert not sidecar.exists()
    backups = list(tmp_path.glob('.wfrl-down-*/*'))
    old = next(path for path in backups if path.read_bytes() == b'old')
    assert str(old) in str(raised.value)


@pytest.mark.parametrize('boundary', ['displacement', 'publication'])
@pytest.mark.parametrize('external_write', [False, True])
def test_interrupt_after_successful_file_move_preserves_old_bytes(tmp_path, monkeypatch, boundary, external_write):
    target = _movie(tmp_path, 'clip.mp4', b'old')
    movie = _movie(tmp_path, 'rendered.mp4', b'new')
    external = _movie(tmp_path, 'external.mp4', b'external')
    sidecar = tmp_path / 'clip.json'
    replace, commit = os.replace, video_output._commit_export
    interrupted = False

    def interrupt():
        nonlocal interrupted
        interrupted = True
        if external_write:
            replace(external, target)
        raise KeyboardInterrupt('Interrupted immediately after the move')

    def replace_with_interrupt(source, destination):
        replace(source, destination)
        if not interrupted and Path(source) == target:
            interrupt()

    def commit_with_interrupt(source, destination):
        commit(source, destination)
        if not interrupted and Path(destination) == target:
            interrupt()

    if boundary == 'displacement':
        monkeypatch.setattr(os, 'replace', replace_with_interrupt)
    else:
        monkeypatch.setattr(video_output, '_commit_export', commit_with_interrupt)
    if external_write:
        with pytest.raises(RuntimeError) as raised:
            video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频')
        assert target.read_bytes() == b'external'
        backup = next(path for path in tmp_path.glob('.wfrl-down-*/*') if path.read_bytes() == b'old')
        assert str(backup) in str(raised.value)
    else:
        with pytest.raises(KeyboardInterrupt, match='Interrupted immediately'):
            video_output.publish_staged([(movie, target)], sidecar, {}, 'Down 视频')
        assert target.read_bytes() == b'old'
        assert not list(tmp_path.glob('.wfrl-down-*'))
    assert interrupted and not sidecar.exists()


@pytest.mark.skipif(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0),
                    reason='directory write permission is not enforced')
def test_failure_on_a_later_target_keeps_earlier_files_unchanged(tmp_path):
    render, out, locked = tmp_path / 'render', tmp_path / 'out', tmp_path / 'locked'
    for directory in (render, out, locked):
        directory.mkdir()
    (out / 'clip.mp4').write_bytes(b'old')
    (out / 'clip.json').write_text('{"old": true}\n', encoding='utf-8')
    movies = [(_movie(render, 'a.mp4', b'center'), out / 'clip.mp4'),
              (_movie(render, 'b.mp4', b'right'), locked / 'clip_right.mp4')]
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(RuntimeError, match='三路视频写出失败') as raised:
            video_output.publish_staged(movies, out / 'clip.json', {'frames': 3}, '三路视频')
    finally:
        locked.chmod(stat.S_IRWXU)
    assert isinstance(raised.value.__cause__, OSError)
    assert (out / 'clip.mp4').read_bytes() == b'old'
    assert json.loads((out / 'clip.json').read_text(encoding='utf-8')) == {'old': True}
    assert sorted(p.name for p in out.iterdir()) == ['clip.json', 'clip.mp4']
    assert not any(locked.iterdir())


def test_json_failure_keeps_previous_movie(tmp_path, monkeypatch):
    render, out = tmp_path / 'render', tmp_path / 'out'
    render.mkdir()
    out.mkdir()
    (out / 'clip.mp4').write_bytes(b'old')
    def failing(path, sidecar):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(atomic_json, 'write_json', failing)
    with pytest.raises(RuntimeError, match='Down 视频写出失败'):
        video_output.publish_staged([(_movie(render, 'a.mp4', b'new'), out / 'clip.mp4')],
                                    out / 'clip.json', {}, 'Down 视频')
    assert (out / 'clip.mp4').read_bytes() == b'old'
    assert sorted(p.name for p in out.iterdir()) == ['clip.mp4']


def test_copy_failure_removes_incomplete_private_stage(tmp_path, monkeypatch):
    render, out = tmp_path / 'render', tmp_path / 'out'
    render.mkdir()
    out.mkdir()
    target = _movie(out, 'clip.mp4', b'old')
    movie = _movie(render, 'rendered.mp4', b'new')

    def fail_after_partial_copy(source, destination):
        Path(destination).write_bytes(b'incomplete')
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(video_output.shutil, 'move', fail_after_partial_copy)
    with pytest.raises(RuntimeError, match='Down 视频写出失败'):
        video_output.publish_staged([(movie, target)], out / 'clip.json', {}, 'Down 视频')
    assert target.read_bytes() == b'old'
    assert list(out.iterdir()) == [target]
