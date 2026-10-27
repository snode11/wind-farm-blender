"""Owned background render processes, progress and cancellation without Blender."""
import importlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

import pytest


@pytest.fixture
def renderer(tmp_path):
    executable = tmp_path / 'blender-fixture'
    executable.write_text('#!' + sys.executable + '\n' + '''import json,os,sys,time
from pathlib import Path
path=Path(sys.argv[-1]); job=json.loads(path.read_text()); root=path.parent
(root/'runtime-profile.json').write_text(json.dumps(dict(config=os.environ.get('BLENDER_USER_CONFIG'))))
mode=os.environ.get('WFRL_TEST_RENDER_MODE','complete')
total=len(range(job['frame_start'],job['frame_end']+1,job['frame_step']))
record=dict(job_id=job['job_id'],state='RUNNING',completed_frames=1,total_frames=total)
def write():
    temp=root/'fixture.tmp'; temp.write_text(json.dumps(record)); temp.replace(root/'manifest.json')
write()
if mode=='wait': time.sleep(30)
record.update(state='COMPLETE',completed_frames=total if mode!='partial' else 1)
write()
raise SystemExit(9 if mode=='fail' else 0)
''')
    executable.chmod(0o700)
    addon = tmp_path / 'addon'
    addon.mkdir()
    (addon / '__init__.py').write_text('')
    (addon / 'animation_render_worker.py').write_text('')
    return executable, addon


def start(tmp_path, renderer):
    module = importlib.import_module('wfrl_blender.animation_render')
    job = module.RenderJob()
    executable, addon = renderer
    job.start(tmp_path / 'output', blender=executable, addon_root=addon, scene_name='source',
              frame_start=2, frame_end=6, frame_step=2,
              snapshot_writer=lambda path: path.write_bytes(b'NON-BLEND snapshot fixture'))
    return job


def wait(job, predicate):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        job.poll()
        if predicate(job):
            return
        time.sleep(.02)
    raise AssertionError(f'render did not reach expected state: {job.state} / {job.error}')


def test_background_job_finishes_in_its_own_output_folder(tmp_path, renderer):
    target = tmp_path / 'output'
    target.mkdir()
    existing = target / 'frame_0002.png'
    existing.write_bytes(b'user data')
    job = start(tmp_path, renderer)
    wait(job, lambda current: not current.active)
    assert job.state == 'COMPLETE'
    assert job.completed_frames == job.total_frames == 3
    assert existing.read_bytes() == b'user data'
    assert job.directory.parent == target
    assert json.loads((job.directory / 'job.json').read_text())['scene_name'] == 'source'
    assert json.loads((job.directory / 'manifest.json').read_text())['state'] == 'COMPLETE'
    profile = json.loads((job.directory / 'runtime-profile.json').read_text())['config']
    assert str(job.directory) in str(profile)


@pytest.mark.parametrize('mode', ['fail', 'partial'])
def test_completion_requires_successful_process_and_all_frames(tmp_path, renderer, monkeypatch, mode):
    monkeypatch.setenv('WFRL_TEST_RENDER_MODE', mode)
    job = start(tmp_path, renderer)
    wait(job, lambda current: not current.active)
    assert job.state == 'FAILED'
    assert job.error
    assert json.loads((job.directory / 'manifest.json').read_text())['state'] == 'FAILED'


@pytest.mark.parametrize('initial', ['running', 'missing', 'incomplete_json'])
def test_poll_rereads_final_manifest_after_process_exit(tmp_path, initial):
    module = importlib.import_module('wfrl_blender.animation_render')
    job = module.RenderJob()
    job.directory = tmp_path
    job.total_frames = 3
    job._record = dict(job_id='render-fixture', state='RUNNING', completed_frames=0, total_frames=3)
    manifest = tmp_path / 'manifest.json'
    if initial == 'running':
        manifest.write_text(json.dumps(dict(job._record, completed_frames=2)))
    elif initial == 'incomplete_json':
        manifest.write_text('{')

    class CompletingProcess:
        def poll(self):
            manifest.write_text(json.dumps(dict(job_id='render-fixture', state='COMPLETE',
                                                completed_frames=3, total_frames=3)))
            return 0

    job.process = CompletingProcess()
    job.poll()

    assert job.state == 'COMPLETE'
    assert job.completed_frames == 3
    assert not job.active
    saved = json.loads(manifest.read_text())
    assert saved['state'] == 'COMPLETE'
    assert saved['completed_frames'] == 3


def test_cancel_is_nonblocking_and_only_stops_owned_process(tmp_path, renderer, monkeypatch):
    monkeypatch.setenv('WFRL_TEST_RENDER_MODE', 'wait')
    job = start(tmp_path, renderer)
    try:
        wait(job, lambda current: current.completed_frames == 1)
        started = time.monotonic()
        job.cancel()
        assert time.monotonic() - started < .2
        wait(job, lambda current: not current.active)
        assert job.state == 'CANCELLED'
        assert job.completed_frames == 1
        assert json.loads((job.directory / 'manifest.json').read_text())['state'] == 'CANCELLED'
        job.cancel()
    finally:
        job.close()


def test_snapshot_failure_never_launches_renderer(tmp_path, renderer):
    module = importlib.import_module('wfrl_blender.animation_render')
    job = module.RenderJob()
    executable, addon = renderer

    def unavailable(_path):
        raise OSError('snapshot unavailable')

    with pytest.raises(OSError, match='snapshot unavailable'):
        job.start(tmp_path / 'output', blender=executable, addon_root=addon, scene_name='source',
                  frame_start=1, frame_end=2, frame_step=1, snapshot_writer=unavailable)
    assert not job.active
    assert job.state == 'FAILED'
    assert json.loads((job.directory / 'manifest.json').read_text())['state'] == 'FAILED'


def test_new_render_cannot_replace_an_active_job(tmp_path, renderer, monkeypatch):
    monkeypatch.setenv('WFRL_TEST_RENDER_MODE', 'wait')
    job = start(tmp_path, renderer)
    try:
        directory = job.directory
        executable, addon = renderer
        with pytest.raises(ValueError):
            job.start(tmp_path / 'other', blender=executable, addon_root=addon, scene_name='other',
                      frame_start=1, frame_end=2, frame_step=1,
                      snapshot_writer=lambda path: path.write_bytes(b'other'))
        assert job.directory == directory
    finally:
        job.close()


def test_compositor_file_outputs_are_rejected_recursively_before_render():
    module = importlib.import_module('wfrl_blender.animation_render')
    output = NS(type='OUTPUT_FILE', mute=False)
    tree = NS(nodes=[NS(type='GROUP', mute=False, node_tree=NS(nodes=[output]))])
    scene = NS(render=NS(use_compositing=True), compositing_node_group=tree)
    with pytest.raises(ValueError, match='文件输出'):
        module.validate_output_nodes(scene)
    output.mute = True
    module.validate_output_nodes(scene)
    output.mute = False
    scene.render.use_compositing = False
    module.validate_output_nodes(scene)
