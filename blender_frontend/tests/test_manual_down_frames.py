"""Per-frame Down samples recorded from frame_change_pre during render.opengl exports."""
from types import SimpleNamespace as NS

import pytest

FRAMES, START = 4, 10


@pytest.fixture
def opengl_job(frontend_module, stub_module, monkeypatch):
    stub_module('bpy_extras.io_utils', ExportHelper=type('ExportHelper', (), {}),
                ImportHelper=type('ImportHelper', (), {}))
    down = frontend_module('wfrl_blender.manual_down')
    evaluated = []

    def apply_motion(_scene, _job, frame):
        evaluated.append(frame)
        return dict(frame=frame, evaluation=len(evaluated))

    monkeypatch.setattr(down, 'apply_motion', apply_motion)
    rows = []
    job = dict(frame_events='OPENGL', frame_start=START, frames_written=0, plan=dict(frames=FRAMES),
               append_sample=rows.append)
    scene = NS(frame_current=START, as_pointer=lambda: 77)
    down._EXPORT[scene.as_pointer()] = job

    def at(frame):
        scene.frame_current = START + frame
        down._frame_handler(scene)

    yield NS(job=job, rows=rows, evaluated=evaluated, at=at)
    down._EXPORT.pop(scene.as_pointer(), None)


def _frames(rows):
    return [row['frame'] for row in rows]


@pytest.mark.parametrize('repeat_first', [False, True])
def test_forward_sequence_appends_each_sample_once_and_leaves_last_pending(opengl_job, repeat_first):
    opengl_job.at(0)  # explicit frame_set(frame_start) before render.opengl
    if repeat_first:
        opengl_job.at(0)  # render.opengl may report the first frame again
    for frame in range(1, FRAMES):
        opengl_job.at(frame)

    job = opengl_job.job
    assert 'frame_error' not in job
    assert _frames(opengl_job.rows) == list(range(FRAMES - 1))
    assert job['frames_written'] == FRAMES - 1
    assert job['pending_sample']['frame'] == FRAMES - 1


def test_repeated_frame_reevaluates_pending_without_appending(opengl_job):
    opengl_job.at(0)
    opengl_job.at(1)
    opengl_job.at(1)

    job = opengl_job.job
    assert 'frame_error' not in job
    assert _frames(opengl_job.rows) == [0] and job['frames_written'] == 1
    assert job['pending_sample'] == dict(frame=1, evaluation=3)


def test_rewind_after_last_frame_keeps_pending_final_sample(opengl_job):
    for frame in range(FRAMES):
        opengl_job.at(frame)
    pending = opengl_job.job['pending_sample']

    opengl_job.at(0)

    job = opengl_job.job
    assert 'frame_error' not in job
    assert job['pending_sample'] is pending and pending['frame'] == FRAMES - 1
    assert job['frames_written'] == FRAMES - 1
    assert _frames(opengl_job.rows) == list(range(FRAMES - 1))
    assert opengl_job.evaluated == list(range(FRAMES))


def test_skipped_frame_records_frame_error(opengl_job):
    opengl_job.at(0)
    opengl_job.at(2)

    job = opengl_job.job
    assert '第 3 帧' in job['frame_error']
    assert opengl_job.rows == [] and job['frames_written'] == 0
