"""Interrupted exports preserve completed artifacts and release owned resources."""
import builtins
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from wfrl.nrel_reconstruction import dataset, diagnostics, evaluate, model, preview
from wfrl.nrel_reconstruction import root_rgb_audit, surface_audit


class InterruptedWriter:
    def __init__(self, handle):
        self.handle = handle

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def write(self, data):
        self.handle.write(data[:max(1, len(data) // 2)])
        self.handle.flush()
        raise OSError("injected interrupted write")


def interrupt_writes(monkeypatch):
    for owner in (builtins, io):
        original = owner.open
        def open_file(file, mode="r", *args, _original=original, **kwargs):
            handle = _original(file, mode, *args, **kwargs)
            return InterruptedWriter(handle) if any(flag in mode for flag in "wax") else handle
        monkeypatch.setattr(owner, "open", open_file)


@pytest.mark.parametrize("kind", ["json", "evaluation_ply", "model_ply", "csv", "gzip_csv"])
@pytest.mark.parametrize("exists", [False, True], ids=["new", "existing"])
def test_interrupted_export_never_publishes_partial_file(tmp_path, monkeypatch, kind, exists):
    target = tmp_path / "result"
    old = b"previous complete artifact"
    if exists:
        target.write_bytes(old)
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    faces = np.array([[0, 1, 2]])
    writers = {
        "json": lambda: dataset.write_json(target, {"score": 1.25}),
        "evaluation_ply": lambda: evaluate.write_ply(target, vertices, faces),
        "model_ply": lambda: model.write_ply(target, vertices, faces),
        "csv": lambda: root_rgb_audit.write_csv(target, [{"score": 1.25}]),
        "gzip_csv": lambda: surface_audit.dump_csv(target, [{"score": 1.25}]),
    }
    interrupt_writes(monkeypatch)
    with pytest.raises(OSError, match="injected interrupted write"):
        writers[kind]()
    assert target.read_bytes() == old if exists else not target.exists()
    assert set(tmp_path.iterdir()) == ({target} if exists else set())


def prepare_models(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    metadata = {"target": "T1/B1", "units": "m", "coordinate_frame": "blade_root_local",
                "surface_scope": "whole_blade", "t_ref_sim_time_s": 1.}
    (root / "model_state.json").write_text(json.dumps(metadata))
    mesh = evaluate.Mesh([[0., 0., 1.], [1., 0., 1.], [0., 1., 2.]], [[0, 1, 2]])
    for name in ("initial_template", "prior_only", "T1_B1"):
        evaluate.write_ply(root / f"{name}.ply", mesh)
    return root, metadata, mesh


@pytest.mark.parametrize("kind", ["score_json", "preview_html", "rgb_comparison"])
def test_report_exports_preserve_previous_content_on_io_error(tmp_path, monkeypatch, kind):
    root, metadata, _ = prepare_models(tmp_path)
    if kind == "score_json":
        target = tmp_path / "scores.json"
        action = lambda: evaluate.evaluate_run(root, tmp_path / "unused.ply",
            {**metadata, "t_ref_sim_time_s": 2.}, output_dir=tmp_path)
    elif kind == "preview_html":
        target = root / "model_preview.html"
        action = lambda: preview.create_preview(root, static_png=False)
    else:
        from wfrl.nrel_reconstruction.compare_rgb import compare_rgb
        labels = tmp_path / "labels.png"
        Image.fromarray(np.zeros((8, 8), dtype=np.uint8)).save(labels)
        item = {"camera_id": "C1", "frame_id": 0, "labels_path": str(labels),
                "K": [[8., 0, 3.5], [0, 8., 3.5], [0, 0, 1.]],
                "T_camera_cv_from_world": np.eye(4), "T_world_from_blade_root": np.eye(4)}
        target = tmp_path / "rgb_comparison.json"
        action = lambda: compare_rgb({"frozen": root / "T1_B1.ply"}, [item], tmp_path,
                                     image_size=(8, 8), reporting_size=(8, 8))
    target.write_bytes(b"complete previous report")
    before = set(tmp_path.rglob("*"))
    interrupt_writes(monkeypatch)
    with pytest.raises(OSError, match="injected interrupted write"):
        action()
    assert target.read_bytes() == b"complete previous report"
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("kind", ["comparison", "preview"])
def test_failed_plot_export_preserves_image_and_closes_figure(tmp_path, monkeypatch, kind):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.figure import Figure
    _, metadata, mesh = prepare_models(tmp_path)
    target = tmp_path / "plot.png"
    target.write_bytes(b"complete previous image")
    before = set(plt.get_fignums())
    def fail_save(self, path, *args, **kwargs):
        Path(path).write_bytes(b"partial PNG")
        raise OSError("injected image encoding error")
    monkeypatch.setattr(Figure, "savefig", fail_save)
    models = {name: mesh for name in ("initial", "prior_only", "final")}
    metrics = {"global": {direction: {"mean_m": 0., "p95_m": 0.} for direction in
               ("reconstruction_to_truth", "truth_to_reconstruction")}, "sections": []}
    try:
        with pytest.raises(OSError, match="injected image encoding error"):
            if kind == "preview":
                preview._static_preview(models, metadata, target)
            else:
                evaluate._plot_comparison({name: metrics for name in models}, target)
        assert set(plt.get_fignums()) == before
        assert target.read_bytes() == b"complete previous image"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["models", "plot.png"]
    finally:
        for figure in set(plt.get_fignums()) - before:
            plt.close(figure)


def test_failed_diagnostic_image_export_is_not_published(tmp_path, monkeypatch):
    root, _, _ = prepare_models(tmp_path)
    labels = tmp_path / "sample_FBU.png"
    rgb = tmp_path / "sample_rgb.jpg"
    Image.fromarray(np.zeros((8, 8), dtype=np.uint8)).save(labels)
    Image.new("RGB", (8, 8)).save(rgb)
    item = {"camera_id": "C1", "frame_id": 0, "labels_path": str(labels), "rgb_path": str(rgb),
            "K": [[8., 0, 3.5], [0, 8., 3.5], [0, 0, 1.]],
            "T_camera_cv_from_world": np.eye(4).tolist(), "T_world_from_blade_root": np.eye(4).tolist()}
    source = tmp_path / "observations.json"
    source.write_text(json.dumps([item]))
    output = tmp_path / "inspection"
    output.mkdir()
    def fail_save(self, path, *args, **kwargs):
        Path(path).write_bytes(b"partial JPEG")
        raise OSError("injected image encoding error")
    monkeypatch.setattr(Image.Image, "save", fail_save)
    with pytest.raises(OSError, match="injected image encoding error"):
        diagnostics.generate_projection_checks(source, root, output, image_size=(8, 8))
    assert not list(output.iterdir())


def test_strict_json_serialization_preserves_previous_file(tmp_path):
    target = tmp_path / "score.json"
    target.write_bytes(b"complete previous report")
    with pytest.raises(ValueError):
        dataset.write_json(target, {"score": float("nan")})
    assert target.read_bytes() == b"complete previous report"
    assert list(tmp_path.iterdir()) == [target]


def test_failed_numpy_export_preserves_previous_archive(tmp_path, monkeypatch):
    from wfrl.nrel_reconstruction.observation_experiment import _save_model
    target = tmp_path / "candidate_exact.npz"
    target.write_bytes(b"previous complete archive")
    def fail_archive(path, **arrays):
        if hasattr(path, "write"):
            path.write(b"partial archive")
        else:
            Path(path).write_bytes(b"partial archive")
        raise OSError("injected archive failure")
    monkeypatch.setattr(np, "savez_compressed", fail_archive)
    with pytest.raises(OSError, match="injected archive failure"):
        _save_model(tmp_path, "candidate", model.BladeModel(span_samples=3, ring_samples=6), {})
    assert target.read_bytes() == b"previous complete archive"
    assert {p.name for p in tmp_path.iterdir()} == {"candidate.ply", "candidate_exact.npz"}
