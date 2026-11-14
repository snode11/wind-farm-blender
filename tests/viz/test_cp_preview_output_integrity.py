"""Preview output is published only after successful encoding and cleanup."""

from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.viz import tip_visualizer as preview
from wfrl.viz.tip_geometry import CameraModel, TurbineGeometry


@pytest.fixture
def encoder(monkeypatch):
    state = SimpleNamespace(events=[], write_error=None, release_error=None,
                            capture_error=None, image_result=True, image_error=None,
                            destination=None)

    class Writer:
        def __init__(self, path, *args):
            self.path = Path(path)
            self.path.write_bytes(b"partial video")

        def isOpened(self):
            return True

        def write(self, canvas):
            state.events.append("write")
            if state.destination is not None:
                assert state.destination.read_bytes() == b"old video"
            if state.write_error:
                raise state.write_error

        def release(self):
            state.events.append("writer.release")
            if state.release_error:
                raise state.release_error
            self.path.write_bytes(b"complete video")

    class Capture:
        def __init__(self, path):
            pass

        def isOpened(self):
            return True

        def get(self, prop):
            return {1: 640, 2: 392, 3: 25, 4: 2}[prop]

        def read(self):
            return True, np.zeros((392, 640, 3), dtype=np.uint8)

        def release(self):
            state.events.append("capture.release")
            if state.capture_error:
                raise state.capture_error

    class Visualizer:
        def __init__(self, *args):
            pass

        def set_trails(self, enabled):
            pass

        def feed(self, *args, **kwargs):
            return np.zeros((392, 1280, 3), dtype=np.uint8)

        feed_world = feed

        def flush(self):
            state.events.append("flush")

    def imwrite(path, canvas):
        Path(path).write_bytes(b"complete image" if state.image_result else b"partial image")
        if state.image_error:
            raise state.image_error
        return state.image_result

    cv = SimpleNamespace(VideoWriter=Writer, VideoCapture=Capture,
                         VideoWriter_fourcc=lambda *args: 0, imwrite=imwrite,
                         CAP_PROP_FRAME_WIDTH=1, CAP_PROP_FRAME_HEIGHT=2,
                         CAP_PROP_FPS=3, CAP_PROP_FRAME_COUNT=4,
                         waitKey=lambda delay: ord("q"), imshow=lambda *args: None,
                         namedWindow=lambda name: state.events.append(("window.open", name)),
                         destroyWindow=lambda name: state.events.append(("window.close", name)),
                         destroyAllWindows=lambda: state.events.append("windows.all.close"))
    monkeypatch.setattr(preview, "opencv", lambda: cv)
    monkeypatch.setattr(preview, "cv2", cv)
    monkeypatch.setattr(preview, "TipVisualizer", Visualizer)
    return state


def run_preview(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["tip-preview", "--frames", "2", *map(str, args)])
    preview.main()


@pytest.mark.parametrize("failure", ["write", "release"])
def test_cp_failed_video_keeps_existing_output(tmp_path, monkeypatch, encoder, failure):
    target = tmp_path / "preview.mp4"
    target.write_bytes(b"old video")
    setattr(encoder, f"{failure}_error", RuntimeError("encoder failed"))
    with pytest.raises(RuntimeError, match="encoder failed"):
        run_preview(monkeypatch, "--out", target, "--no-show")
    assert target.read_bytes() == b"old video"
    assert "writer.release" in encoder.events
    assert set(tmp_path.iterdir()) == {target}


def test_cp_video_is_published_after_writer_release(tmp_path, monkeypatch, encoder):
    target = tmp_path / "preview.mp4"
    target.write_bytes(b"old video")
    encoder.destination = target
    run_preview(monkeypatch, "--out", target, "--no-show")
    assert target.read_bytes() == b"complete video"
    assert encoder.events[-1] == "writer.release"
    assert set(tmp_path.iterdir()) == {target}


@pytest.mark.parametrize("failure", ["false", "raise"])
def test_cp_failed_image_keeps_existing_output(tmp_path, monkeypatch, encoder, failure):
    target = tmp_path / "snapshot_000.png"
    target.write_bytes(b"old image")
    encoder.image_result = False
    if failure == "raise":
        encoder.image_error = ValueError("image encoding failed")
    with pytest.raises(ValueError):
        run_preview(monkeypatch, "--dump", tmp_path / "snapshot", "--dump-at", "0", "--no-show")
    assert target.read_bytes() == b"old image"
    assert set(tmp_path.iterdir()) == {target}


def test_cp_successful_image_retains_requested_name(tmp_path, monkeypatch, encoder):
    run_preview(monkeypatch, "--dump", tmp_path / "snapshot", "--dump-at", "0", "--no-show")
    assert (tmp_path / "snapshot_000.png").read_bytes() == b"complete image"


def test_cp_cleanup_attempts_all_resources_and_preserves_primary_error(tmp_path, monkeypatch, encoder):
    dets = tmp_path / "dets.csv"
    dets.write_text("frame,u,v\n0,320,200\n", encoding="utf-8")
    encoder.write_error = RuntimeError("encoding failed")
    encoder.capture_error = OSError("capture release failed")
    with pytest.raises(RuntimeError, match="encoding failed") as error:
        run_preview(monkeypatch, "--video", "input.mp4", "--dets", dets,
                    "--out", tmp_path / "preview.mp4")
    assert encoder.events[-3:] == ["capture.release", "writer.release",
                                  ("window.close", "Tip tracking preview")]
    assert "capture release failed" in " ".join(error.value.__notes__)
    assert not (tmp_path / "preview.mp4").exists()


def test_cp_preview_closes_only_its_owned_window(monkeypatch, encoder):
    run_preview(monkeypatch)
    assert ("window.close", "Tip tracking preview") in encoder.events
    assert "windows.all.close" not in encoder.events


def test_cp_initialization_failure_does_not_close_unowned_windows(tmp_path, monkeypatch, encoder):
    dets = tmp_path / "dets.csv"
    dets.write_text("frame,u,v\n0,nan,200\n", encoding="utf-8")
    with pytest.raises(ValueError):
        run_preview(monkeypatch, "--video", "input.mp4", "--dets", dets)
    assert encoder.events == ["capture.release"]


@pytest.mark.parametrize("mode", ["pixels", "world"])
def test_cp_invalid_frame_does_not_advance_tracking_time(mode):
    geom = TurbineGeometry(110, 5, 5, 68, 2)
    camera = CameraModel.look_at(640, 392, 49.2, 31.36,
                                geom.hub + [-8, 0, -3], geom.tip_position(180),
                                geom.e_lat, upright=True)
    vis = preview.TipVisualizer(geom, camera)
    point = geom.tip_position(180)
    frame = np.zeros((1, 1, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="resolution"):
        if mode == "pixels":
            vis.feed(frame, {0: camera.project(point)[:2]}, t=1)
        else:
            vis.feed_world(frame, {0: {"P": point, "sections": [geom.hub, point]}},
                           t=1, source="test", provenance={"test": True})
    assert vis._last_t is None
    assert not vis.results and not vis.trajs


@pytest.mark.parametrize("text", ["frame,u\n", "", "frame,u,v,u\n0,10,20,30\n",
                                  "frame,u,v\n0,10,20,30\n"])
def test_cp_detection_csv_rejects_ambiguous_or_missing_columns(tmp_path, text):
    path = tmp_path / "dets.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        preview.load_dets(path)
    assert str(path) in str(error.value)


def test_cp_detection_csv_parses_text_numbers_at_file_boundary(tmp_path):
    path = tmp_path / "dets.csv"
    path.write_text(" Frame ,U,v,blade,rpm\n0,320.5,200,0,8\n0,100,50,1,\n",
                    encoding="utf-8")
    assert preview.load_dets(path) == {0: ({0: (320.5, 200.0), 1: (100.0, 50.0)}, 8.0)}

