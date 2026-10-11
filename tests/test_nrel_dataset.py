"""Boundary and media tests for the NREL video-only algorithm handoff."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from wfrl.camera_video.media import sha256
from wfrl.nrel_reconstruction import dataset, pack


def _write_csv(path, rows, fields=None):
    with Path(path).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _resign(root):
    data = json.loads((root / "dataset.json").read_text())
    data["files"] = {name: sha256(root / name) for name in data["files"]}
    dataset.write_json(root / "dataset.json", data)


def _imaging():
    return {"shading":"MATERIAL", "studio_light":"forest.exr", "scene_lights":False,
            "scene_world":False, "studio_rotation":0.0, "studio_intensity":1.0,
            "studio_background_alpha":1.0, "studio_background_blur":0.5,
            "studio_view_rotation":False,
            "color_management":{"view_transform":"AgX", "look":"AgX - Medium High Contrast",
                                "exposure":0.35, "gamma":1.0, "use_curve_mapping":False},
            "display_device":"sRGB",
            "image_encoding":"RGBA8 display-transformed once; top-left rows; PNG sRGB"}


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "algorithm-input"
    root.mkdir()
    for camera in ("C1","C2","C3"):
        (root / f"{camera}.mp4").write_bytes(b"test video bytes; loader does not decode")
    rows, calibrations, motion = [], [], []
    k = [[1000,0,959.5],[0,1000,539.5],[0,0,1]]
    pose = np.eye(4).tolist()
    for camera in ("C1","C2","C3"):
        for frame in range(2):
            rows.append({"camera_id":camera,"frame_id":frame,"video_pts_s":frame/20,
                         "sim_time_s":117+(frame+.5)/20,"valid":1})
            calibrations.append({"camera_id":camera,"frame_id":frame,"width":1920,"height":1080,
                                 "K":k,"T_camera_cv_from_world":pose,
                                 "pixel_convention":"top-left pixel centre (0,0); boundaries -.5,W/H-.5",
                                 "distortion":"ideal undistorted pinhole"})
    for frame in range(2):
        motion.append({"frame_id":frame,"sim_time_s":117+(frame+.5)/20,
                       "T_world_from_blade_root":json.dumps(pose),
                       "source":"simulation ideal rigid running observation; no deformation", "units":"m"})
    _write_csv(root / "frame_map.csv",rows)
    _write_csv(root / "motion_observations.csv",motion)
    (root / "camera_calibration.jsonl").write_text("".join(json.dumps(row)+"\n" for row in calibrations))
    files = ["C1.mp4","C2.mp4","C3.mp4","frame_map.csv","camera_calibration.jsonl","motion_observations.csv"]
    data = {"schema":"nrel-single-blade-video.v1", "target":"T1/B1", "model":"NREL 5MW",
            "units":{"length":"m","time":"s","image":"px"},
            "videos":{camera:f"{camera}.mp4" for camera in ("C1","C2","C3")},
            "files":{name:sha256(root/name) for name in files},
            "sampling":{"start_s":117,"end_s_exclusive":117.1,"fps":20,"frame_count_per_camera":2,
                        "simulation_time":"start+(n+.5)/fps","pts":"n/fps"},
            "input_policy":{"calibration":"simulation ideal pinhole",
                            "rigid_motion":"simulation ideal B1 blade-root rigid pose",
                            "exact_flexible_deformation":False,"truth_surface":False,
                            "truth_masks_or_correspondences":False,"decoded_rgb":"delivered MP4 only"},
            "reference_state":None,"imaging":_imaging()}
    dataset.write_json(root/"dataset.json",data)
    return root


def test_valid_bundle_and_center_schedule(bundle):
    data, calibrations, motions = dataset.load_bundle(bundle)
    assert len(calibrations) == 6
    assert len(motions) == 2
    assert data["sampling"]["frame_count_per_camera"] == 2
    assert pack.center_schedule(117,0.1,20) == pytest.approx([117.025,117.075])
    with pytest.raises(ValueError):
        pack.center_schedule(117,0.1,30)


@pytest.mark.parametrize("container",["videos","sampling","input_policy","imaging"])
def test_nested_unknown_truth_field_is_rejected(bundle,container):
    data = json.loads((bundle/"dataset.json").read_text())
    data[container]["truth_surface"] = "../evaluation-only/truth.ply"
    dataset.write_json(bundle/"dataset.json",data)
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)


def test_video_reference_cannot_name_truth_path(bundle):
    data = json.loads((bundle/"dataset.json").read_text())
    data["videos"]["C1"] = "../evaluation-only/truth.ply"
    dataset.write_json(bundle/"dataset.json",data)
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)


def test_reference_state_unknown_geometry_is_rejected(bundle):
    data = json.loads((bundle/"dataset.json").read_text())
    data["reference_state"] = {"exact_surface_vertices":[[1,2,3]]}
    dataset.write_json(bundle/"dataset.json",data)
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)


@pytest.mark.parametrize("patch",[
    {"t_ref_sim_time_s":117.9}, {"t_ref_sim_time_s":float("nan")}, {"frame_id":2},
    {"fit_frame_ids":[0,1]}, {"heldout_frame_ids":[2]}, {"fit_frame_ids":[0,0]},
])
def test_reference_state_tracks_real_frame_and_nonoverlapping_splits(bundle,patch):
    data = json.loads((bundle/"dataset.json").read_text())
    state = {"t_ref_sim_time_s":117.025,"frame_id":0,"selection_basis":"RGB visibly contains B1",
             "fit_frame_ids":[0],"heldout_frame_ids":[1]}
    data["reference_state"] = {**state,**patch}
    (bundle/"dataset.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)


def test_external_manifest_and_video_symlinks_are_rejected(bundle,tmp_path):
    external = tmp_path/"evaluation-only"
    external.mkdir()
    video = external/"C1.mp4"
    (bundle/"C1.mp4").rename(video)
    (bundle/"C1.mp4").symlink_to(video)
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)
    (bundle/"C1.mp4").unlink()
    video.rename(bundle/"C1.mp4")
    manifest = external/"dataset.json"
    (bundle/"dataset.json").rename(manifest)
    (bundle/"dataset.json").symlink_to(manifest)
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)


@pytest.mark.parametrize("filename",["camera_calibration.jsonl","motion_observations.csv"])
def test_duplicate_metadata_is_not_silently_deduplicated(bundle,filename):
    path = bundle/filename
    lines = path.read_text().splitlines()
    extra = lines[0] if filename.endswith("jsonl") else lines[1]
    path.write_text("\n".join(lines+[extra])+"\n")
    _resign(bundle)
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)


@pytest.mark.parametrize("field,value",[("start_s",float("nan")),("fps",20.5),("frame_count_per_camera",2.5),
                                       ("end_s_exclusive",118.0)])
def test_sampling_is_finite_integral_and_consistent(bundle,field,value):
    data = json.loads((bundle/"dataset.json").read_text())
    data["sampling"][field] = value
    (bundle/"dataset.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)


def test_nan_in_frame_map_is_rejected(bundle):
    path = bundle/"frame_map.csv"
    rows = list(csv.DictReader(path.open()))
    rows[0]["sim_time_s"] = "nan"
    _write_csv(path,rows)
    _resign(bundle)
    with pytest.raises(ValueError):
        dataset.load_bundle(bundle)


def _probe():
    return {"streams":[{"width":1920,"height":1080,"avg_frame_rate":"20/1","time_base":"1/20","codec_name":"h264"}],
            "frames":[{"best_effort_timestamp_time":frame/20,"pts":frame} for frame in range(2)]}


def test_inspection_completes_all_frames_even_if_only_first_saved(bundle,tmp_path,monkeypatch):
    decoded = []
    def frames(path,width,height):
        for i in range(2):
            decoded.append((path.name,i))
            yield np.zeros((height,width,3),dtype=np.uint8)
    monkeypatch.setattr(dataset,"probe_video",lambda path:_probe())
    monkeypatch.setattr(dataset,"iter_rgb",frames)
    report = dataset.decode_inspection(bundle,tmp_path/"inspection",selected_ids=[0])
    assert len(decoded) == 6
    assert all(item["frame_count"] == 2 for item in report["videos"].values())
    assert len(list((tmp_path/"inspection").glob("C?_000000.jpg"))) == 3


def test_inspection_rejects_actual_video_calibration_dimension_mismatch(bundle,tmp_path,monkeypatch):
    def altered_probe(path):
        result = _probe()
        result["streams"][0]["width"] = 1918
        return result
    monkeypatch.setattr(dataset,"probe_video",altered_probe)
    monkeypatch.setattr(dataset,"iter_rgb",lambda path,width,height:iter([np.zeros((height,width,3),dtype=np.uint8)]*2))
    with pytest.raises(ValueError):
        dataset.decode_inspection(bundle,tmp_path/"inspection",selected_ids=[0])


def test_inspection_rejects_truncated_full_decode(bundle,tmp_path,monkeypatch):
    monkeypatch.setattr(dataset,"probe_video",lambda path:_probe())
    monkeypatch.setattr(dataset,"iter_rgb",lambda path,width,height:iter([np.zeros((height,width,3),dtype=np.uint8)]))
    with pytest.raises(ValueError,match="frame count"):
        dataset.decode_inspection(bundle,tmp_path/"inspection",selected_ids=[0])


def test_pack_reuses_png_symlink_and_whitelists_generated_metadata(tmp_path,monkeypatch):
    capture = tmp_path/"internal-capture"
    capture.mkdir()
    dataset.write_json(capture/"manifest.json",{"status":"complete","render_profile":_imaging(),"truth_surface":[[1,2,3]]})
    records, motions = [], []
    for frame,t in enumerate(pack.center_schedule(117,0.1,20)):
        for camera in ("C1","C2","C3"):
            (capture/camera).mkdir(exist_ok=True)
            (capture/camera/f"frame_{frame:06d}.png").write_bytes(b"immutable test PNG")
            records.append({"camera_id":camera,"sample_index":frame,"time_s":t,"capture_status":"complete",
                            "simulation_state_hash":f"same-at-{frame}","image_width":1920,"image_height":1080,
                            "K":[[1000,0,959.5],[0,1000,539.5],[0,0,1]],"T_camera_cv_from_world":np.eye(4).tolist(),
                            "truth_vertices":[[1,2,3]]})
        motions.append({"sample_index":frame,"sim_time_s":t,"includes_deformation":False,
                        "T_world_from_blade_root":np.eye(4).tolist(),"flexible_truth":[1,2,3]})
    (capture/"frames.jsonl").write_text("".join(json.dumps(row)+"\n" for row in records))
    (capture/"motion_internal.jsonl").write_text("".join(json.dumps(row)+"\n" for row in motions))
    def encode(adapter,fps,**kwargs):
        masters = adapter/"master_frames"
        assert masters.is_symlink()
        assert masters.resolve().parent == capture
        (adapter/"video.mp4").write_bytes(b"encoded video")
        return {"width":1920,"height":1080,"valid":True,"frame_count":2}
    monkeypatch.setattr(pack,"encode_video",encode)
    out = tmp_path/"run"
    report = pack.pack_capture(capture,out,117,0.1,20)
    assert report["png_total_bytes"] == 6*len(b"immutable test PNG")
    assert not list(out.rglob("*.png"))
    data,_,_ = dataset.load_bundle(out/"algorithm-input")
    assert "truth_vertices" not in (out/"algorithm-input"/"camera_calibration.jsonl").read_text()
    assert "flexible_truth" not in (out/"algorithm-input"/"motion_observations.csv").read_text()
    assert data["input_policy"]["truth_surface"] is False


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),reason="FFmpeg required for real decode")
def test_real_mp4_stream_is_fully_decoded(tmp_path):
    video = tmp_path/"tiny.mp4"
    subprocess.run(["ffmpeg","-v","error","-nostdin","-f","lavfi","-i","testsrc=size=16x16:rate=20",
                    "-frames:v","3","-an","-c:v","libx264","-pix_fmt","yuv420p","-bf","0",str(video)],check=True)
    info = dataset.probe_video(video)
    frames = list(dataset.iter_rgb(video,16,16))
    assert len(frames) == len(info["frames"]) == 3
    assert all(rgb.shape == (16,16,3) and rgb.dtype == np.uint8 for rgb in frames)
