"""Staged publication of manual Down exports: all targets replaced, or none changed."""
import json
import os
from pathlib import Path
import stat

import pytest

from wfrl_blender import video_output


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
    original = Path.write_text

    def failing(self, *args, **kwargs):
        if self.name.endswith('.json.partial'):
            raise OSError(28, 'No space left on device')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', failing)
    with pytest.raises(RuntimeError, match='Down 视频写出失败'):
        video_output.publish_staged([(_movie(render, 'a.mp4', b'new'), out / 'clip.mp4')],
                                    out / 'clip.json', {}, 'Down 视频')
    assert (out / 'clip.mp4').read_bytes() == b'old'
    assert sorted(p.name for p in out.iterdir()) == ['clip.mp4']
