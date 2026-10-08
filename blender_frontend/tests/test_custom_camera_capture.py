from pathlib import Path
import io
import json
import sys
from types import SimpleNamespace as NS
import pytest
from PIL import Image
from wfrl_blender import custom_camera_capture as capture
from wfrl_blender import custom_camera_preview as preview
from wfrl_blender import custom_cameras as core
from wfrl_blender.custom_camera_capture import write_png,write_json


def test_png_preserves_display_bytes_and_flips_only_rows(tmp_path):
    # GPU rows start at lower left, PNG rows at upper left. Explicit asymmetric
    # colour/alpha fixture detects gamma reapplication, channels and orientation.
    bottom=bytes([0,0,255,255,17,91,143,128])
    top=bytes([255,0,0,255,0,255,0,255])
    target=tmp_path/'image.png'
    write_png(target,2,2,bottom+top)
    with Image.open(target) as image:
        assert list(image.getdata())==[(255,0,0,255),(0,255,0,255),(0,0,255,255),(17,91,143,128)]
    with pytest.raises(FileExistsError):write_png(target,2,2,bottom+top)
    with pytest.raises(ValueError):write_png(tmp_path/'bad.png',3,3,bottom)


def test_manifest_rejects_nan_and_preserves_committed_version(tmp_path):
    target=tmp_path/'manifest.json'
    write_json(target,{'status':'incomplete'})
    with pytest.raises(ValueError):write_json(target,{'time_s':float('nan')})
    assert json.loads(target.read_text())=={'status':'incomplete'}


@pytest.mark.parametrize('termination', ['success', 'cancel', 'exception', 'invalid_session'])
def test_capture_closes_gpu_resources_for_every_termination(tmp_path, termination, monkeypatch):
    job = capture.Capture.__new__(capture.Capture)
    job.closed = job.in_group = job.cancel_requested = False
    job.cancel_accepted_at = job.cancel_stopped_at = None
    job.index, job.times, job.error = 0, [0, 1], ''
    job.path, job.manifest = tmp_path, {'status': 'incomplete'}
    job.render_target = preview.RenderTargetPool()
    freed = []
    job.render_target.targets[(10, 10)] = NS(releases=0)
    def release():
        freed.append(True)
        job.render_target.targets[(10, 10)].releases += 1
    job.render_target.targets[(10, 10)].free = release
    monkeypatch.setattr(job, 'same_session', lambda: termination != 'invalid_session')
    # The restoration mechanism is independently exercised in native tests.
    monkeypatch.setattr(job, '_play', lambda *_: None)
    job.sequence, job.original_time_property, job.scene, job.playing = False, None, {}, False
    monkeypatch.setattr(capture, '_ACTIVE', job)
    if termination == 'exception':
        def failure(_context):
            raise RuntimeError('injected GPU failure')
        monkeypatch.setattr(job, '_step_group', failure)
        with pytest.raises(RuntimeError, match='injected GPU failure'):
            job.step(None)
    elif termination == 'cancel':
        job.request_cancel()
    else:
        job.finish(success=termination == 'success')
    assert job.closed and not capture.active() and freed == [True]
    manifest = json.loads((tmp_path/'manifest.json').read_text())
    assert manifest['gpu_target_usage']['active_targets'] == 0
    assert manifest['gpu_target_usage']['releases'] == 1
    assert manifest['status'] == ('complete' if termination == 'success' else 'incomplete')
    job.finish()
    assert freed == [True]


def test_guard_reports_invalid_native_pose_before_layout_hashing(monkeypatch):
    job = capture.Capture.__new__(capture.Capture)
    job.scene = NS(frame_current=1, frame_subframe=0)
    job.window = NS(screen=NS(is_animation_playing=False))
    job.expected_frame = (1, 0)
    monkeypatch.setattr(job, 'same_session', lambda: True)
    monkeypatch.setitem(sys.modules, core.__package__+'.panels.custom_cameras',
                        NS(installation_block_reason=lambda _scene: ''))
    def invalid(_scene):
        raise ValueError('C1 实际姿态含非有限数值')
    def unexpected(_scene):
        raise AssertionError('layout hashing ran on invalid native state')
    monkeypatch.setattr(core, 'validate_native_state', invalid)
    monkeypatch.setattr(core, 'layout_dict', unexpected)
    with pytest.raises(RuntimeError, match='C1 实际姿态含非有限数值'):
        job.guard()
