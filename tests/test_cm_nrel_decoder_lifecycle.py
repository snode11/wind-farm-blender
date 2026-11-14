"""Streaming decoder failures release pipes and cannot deadlock on stderr."""
import io
import subprocess

import numpy as np
import pytest

from wfrl.nrel_reconstruction import dataset


class Decoder:
    def __init__(self):
        self.stdout = io.BytesIO(bytes(12))
        self.stderr = io.BytesIO()
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        return 0 if self.killed else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if not self.killed:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return -9


def test_closing_rgb_generator_bounds_termination_wait_and_closes_pipes(monkeypatch):
    decoder = Decoder()
    stderr_targets = []
    def launch(args, **kwargs):
        stderr_targets.append(kwargs["stderr"])
        return decoder
    monkeypatch.setattr(dataset.subprocess, "Popen", launch)
    frames = dataset.iter_rgb("sample.mp4", 2, 2)
    assert next(frames).shape == (2, 2, 3)
    frames.close()
    assert decoder.terminated and decoder.killed
    assert decoder.waits and all(timeout is not None for timeout in decoder.waits)
    assert decoder.stdout.closed
    assert stderr_targets[0] != subprocess.PIPE
    assert stderr_targets[0].closed


def test_rgb_error_output_is_not_an_unread_pipe(monkeypatch):
    class FailedDecoder:
        stdout = io.BytesIO()
        stderr = io.BytesIO(b"invalid input")
        def poll(self):
            return 1
        def wait(self, timeout=None):
            return 1
    targets = []
    def launch(args, **kwargs):
        target = kwargs["stderr"]
        targets.append(target)
        if target != subprocess.PIPE:
            target.write(b"decoder failed " * 100000)
        return FailedDecoder()
    monkeypatch.setattr(dataset.subprocess, "Popen", launch)
    with pytest.raises(ValueError, match="decode failed"):
        list(dataset.iter_rgb("corrupt.mp4", 2, 2))
    assert targets[0] != subprocess.PIPE
    assert targets[0].closed


@pytest.mark.parametrize("width,height", [(0, 2), (-1, 2), (2, 0), (2.5, 2)])
def test_rgb_dimensions_rejected_before_process_launch(monkeypatch, width, height):
    launches = []
    monkeypatch.setattr(dataset.subprocess, "Popen", lambda *args, **kwargs: launches.append(args))
    with pytest.raises(ValueError, match="dimensions"):
        list(dataset.iter_rgb("sample.mp4", width, height))
    assert launches == []


def test_nonfinite_video_pts_cannot_pass_inspection(tmp_path, monkeypatch):
    sampling = {"frame_count_per_camera": 1, "fps": 20}
    calibration = {(camera, 0): {"width": 8, "height": 8} for camera in ("C1", "C2", "C3")}
    monkeypatch.setattr(dataset, "load_bundle", lambda path: ({"sampling": sampling, "input_policy": {}},
                                                             calibration, {0: {"sim_time_s": .025}}))
    monkeypatch.setattr(dataset, "probe_video", lambda path: {"streams": [{"width": 8, "height": 8}],
                          "frames": [{"best_effort_timestamp_time": "nan"}]})
    monkeypatch.setattr(dataset, "iter_rgb", lambda *args: iter([np.zeros((8, 8, 3), dtype=np.uint8)]))
    monkeypatch.setattr(dataset, "sha256", lambda path: "dummy")
    monkeypatch.setattr(dataset.subprocess, "run", lambda *args, **kwargs: type("Version", (), {"stdout": "ffmpeg\n"})())
    with pytest.raises(ValueError, match="PTS"):
        dataset.decode_inspection(tmp_path, tmp_path / "inspection", [0])
