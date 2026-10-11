#!/usr/bin/env python3
"""DY1 RGB inspection only: frozen inputs, OS isolation, no inferred identity.

The launch command creates a fresh output, freezes its exact input list, then
starts a sandboxed worker. No segmentation, fitting, truth or Blender imports.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CAMERAS = ("C1", "C2", "C3")
INPUT_FILES = {"dataset.json", "frame_map.csv", "camera_calibration.jsonl",
               "motion_observations.csv", "C1.mp4", "C2.mp4", "C3.mp4"}
POLICY = {"calibration": "simulation ideal pinhole",
          "rigid_motion": "simulation ideal B1 blade-root rigid pose",
          "exact_flexible_deformation": False, "truth_surface": False,
          "truth_masks_or_correspondences": False,
          "decoded_rgb": "delivered MP4 only"}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                    allow_nan=False) + "\n")


def validate_pts(frames, count, fps):
    if len(frames) != count:
        raise ValueError("Actual decoded frame count does not match frozen input")
    pts = [float(frame["best_effort_timestamp_time"]) for frame in frames]
    if any(not math.isfinite(t) or abs(t - i / fps) > 1e-6
           for i, t in enumerate(pts)):
        raise ValueError("Actual frame PTS does not match declared n/fps")
    return pts


def unreviewed_row(camera, frame, pts, sim_time):
    return {"camera_id": camera, "frame_id": frame, "video_pts_s": pts,
            "image_sample_center_sim_time_s": sim_time,
            "b1_identity": "UNREVIEWED", "image_visible": "UNREVIEWED",
            "unoccluded": "UNREVIEWED", "trusted_contour": "UNREVIEWED",
            "complete_surface_tip_visible": "UNREVIEWED",
            "structural_point_constraint": "NOT_ESTABLISHED",
            "d_constraint": "NOT_ESTABLISHED", "d_m": None,
            "reviewer": None, "review_notes": None}


def sandbox_policy(input_files, output, runtime):
    # Deny repository data by default, including all old results and outputs.
    # Runtime/system reads remain available to macOS dyld and Python. Only the
    # exact source and signed input files are exceptions within the repository.
    subpaths = [str(output)]
    clauses = [f'(subpath {json.dumps(p)})' for p in subpaths]
    clauses += [f'(literal {json.dumps(str(p))})' for p in input_files]
    allowed = "\n        ".join(clauses)
    return ("(version 1)\n(allow default)\n(deny network*)\n"
            f"(deny file-read-data (require-all (subpath {json.dumps(str(ROOT))})\n"
            "    (require-not (require-any\n        " + allowed + "))))\n"
            f"(deny file-read-data (subpath {json.dumps(str(Path.home() / 'Library/Application Support/Blender'))}))\n"
            "(deny file-write* (require-not (require-any\n"
            f"    (subpath {json.dumps(str(output))}) (literal \"/dev/null\"))))\n")


def isolation_probe(output, input_dir):
    denied_paths = [
        ROOT / "README.md",
        ROOT / "blender_frontend/wfrl_blender/assets/mappo/geometry.npz",
        ROOT / "results",
        ROOT / "outputs/nrel-video-single-blade/stages/02-second-run/evaluation-only/isolation-marker.txt",
        Path.home() / "Library/Application Support/Blender",
    ]
    probes = []
    for path in denied_paths:
        try:
            # Open only; never read prohibited contents even if policy is wrong.
            descriptor = os.open(path, os.O_RDONLY)
        except OSError as exc:
            probes.append({"path": str(path), "errno": exc.errno,
                           "status": "DENIED" if exc.errno in (errno.EPERM, errno.EACCES)
                           else "INCONCLUSIVE"})
        else:
            os.close(descriptor)
            probes.append({"path": str(path), "status": "UNEXPECTEDLY_ALLOWED"})
    with (input_dir / "dataset.json").open("rb") as stream:
        allowed = bool(stream.read(1))
    with socket.socket() as connection:
        connection.settimeout(1)
        try:
            connection.connect(("127.0.0.1", 9))
        except OSError as exc:
            network = {"status": "DENIED" if exc.errno in (errno.EPERM, errno.EACCES)
                       else "INCONCLUSIVE", "errno": exc.errno}
        else:
            network = {"status": "UNEXPECTEDLY_ALLOWED"}
    passed = allowed and all(p["status"] == "DENIED" for p in probes) and network["status"] == "DENIED"
    result = {"passed": passed, "allowed_input_read": allowed,
              "file_probes": probes, "network_probe": network,
              "prohibited_contents_read": False}
    write_json(output / "isolation_probe.json", result)
    if not passed:
        raise RuntimeError("Isolation probes did not all demonstrate denial")
    return result


def load_input(input_dir):
    import numpy as np
    data = json.loads((input_dir / "dataset.json").read_text())
    if (data.get("schema") != "nrel-single-blade-video.v1" or
            data.get("target") != "T1/B1" or data.get("model") != "NREL 5MW" or
            data.get("units") != {"length": "m", "time": "s", "image": "px"} or
            data.get("input_policy") != POLICY or
            set(data.get("files", {})) != INPUT_FILES - {"dataset.json"}):
        raise ValueError("Unexpected input contract")
    expected_sampling = {"start_s": 120, "end_s_exclusive": 125, "fps": 20,
                         "frame_count_per_camera": 100,
                         "simulation_time": "start+(n+.5)/fps", "pts": "n/fps"}
    if data["sampling"] != expected_sampling:
        raise ValueError("Audit budget is frozen to the formal 100-frame input")
    for name, signature in data["files"].items():
        path = input_dir / name
        if path.resolve().parent != input_dir or digest(path) != signature:
            raise ValueError(f"Input signature/path mismatch: {name}")
    with (input_dir / "frame_map.csv").open() as stream:
        frames = list(csv.DictReader(stream))
    calibrations = [json.loads(line) for line in
                    (input_dir / "camera_calibration.jsonl").read_text().splitlines()]
    with (input_dir / "motion_observations.csv").open() as stream:
        motions = list(csv.DictReader(stream))
    cal = {(c["camera_id"], int(c["frame_id"])): c for c in calibrations}
    motion = {int(m["frame_id"]): m for m in motions}
    if (len(frames) != 300 or len(calibrations) != 300 or len(cal) != 300 or
            len(motions) != 100 or set(motion) != set(range(100))):
        raise ValueError("Incomplete or duplicate input frame/pose records")
    expected = {(c, i) for c in CAMERAS for i in range(100)}
    if set(cal) != expected or {(f["camera_id"], int(f["frame_id"])) for f in frames} != expected:
        raise ValueError("Invalid camera/frame identities")
    for frame in frames:
        camera, i = frame["camera_id"], int(frame["frame_id"])
        if (frame["valid"] != "1" or abs(float(frame["video_pts_s"]) - i / 20) > 1e-8 or
                abs(float(frame["sim_time_s"]) - (120 + (i + .5) / 20)) > 1e-8):
            raise ValueError("Invalid declared time mapping")
        c, m = cal[camera, i], motion[i]
        if c["width"] != 1920 or c["height"] != 1080 or m["units"] != "m":
            raise ValueError("Invalid dimensions or units")
        if abs(float(m["sim_time_s"]) - float(frame["sim_time_s"])) > 1e-8:
            raise ValueError("Motion/image time mismatch")
        k = np.asarray(c["K"], dtype=float)
        if (k.shape != (3, 3) or not np.isfinite(k).all() or
                not np.allclose(k[2], [0, 0, 1]) or k[0, 0] <= 0 or k[1, 1] <= 0):
            raise ValueError("Invalid camera intrinsics")
        for value in (c["T_camera_cv_from_world"], json.loads(m["T_world_from_blade_root"])):
            matrix = np.asarray(value, dtype=float)
            if (matrix.shape != (4, 4) or not np.isfinite(matrix).all() or
                    not np.allclose(matrix[3], [0, 0, 0, 1]) or
                    not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-3) or
                    not np.isclose(np.linalg.det(matrix[:3, :3]), 1, atol=1e-3)):
                raise ValueError("Invalid ideal rigid transform")
    return data, cal, motion


def iter_rgb(video, width, height):
    process = subprocess.Popen(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-nostdin",
                                "-i", str(video), "-an", "-f", "rawvideo", "-pix_fmt",
                                "rgb24", "-threads", "1", "pipe:1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = width * height * 3
    try:
        while True:
            frame = bytearray()
            while len(frame) < size:
                part = process.stdout.read(size - len(frame))
                if not part:
                    break
                frame.extend(part)
            if not frame:
                break
            if len(frame) != size:
                raise ValueError("Truncated decoded RGB frame")
            yield bytes(frame)
        error = process.stderr.read().decode(errors="replace")
        if process.wait() != 0:
            raise ValueError(f"FFmpeg failed: {error}")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()
        process.stdout.close()
        process.stderr.close()


def worker(output):
    started = time.perf_counter()
    freeze = json.loads((output / "launch_freeze.json").read_text())
    input_dir = Path(freeze["input_root"])
    isolation_probe(output, input_dir)
    for name, signature in freeze["input_sha256"].items():
        if digest(name) != signature:
            raise ValueError(f"Launch-frozen input changed: {name}")
    from PIL import Image, ImageDraw
    data, calibration, motion = load_input(input_dir)
    for directory in ("thumbnails", "native", "contact"):
        (output / directory).mkdir()
    rows, videos = [], {}
    for camera in CAMERAS:
        camera_started = time.perf_counter()
        video = input_dir / f"{camera}.mp4"
        args = ["/opt/homebrew/bin/ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_streams", "-show_frames", "-show_entries",
                "stream=width,height,avg_frame_rate,time_base,codec_name:frame=pts,best_effort_timestamp_time",
                "-of", "json", str(video)]
        info = json.loads(subprocess.run(args, check=True, capture_output=True, text=True).stdout)
        write_json(output / f"{camera}_ffprobe.json", info)
        stream = info["streams"][0]
        if (stream["width"], stream["height"]) != (1920, 1080):
            raise ValueError("Actual video size differs from calibration")
        pts = validate_pts(info["frames"], 100, 20)
        tiles, count = [], 0
        for i, rgb in enumerate(iter_rgb(video, 1920, 1080)):
            if i >= 100:
                raise ValueError("RGB decoder produced too many frames")
            im = Image.frombytes("RGB", (1920, 1080), rgb)
            row = unreviewed_row(camera, i, pts[i], float(motion[i]["sim_time_s"]))
            row["decoded_rgb_sha256"] = hashlib.sha256(rgb).hexdigest()
            row["source_video_sha256"] = data["files"][f"{camera}.mp4"]
            if i in freeze["native_frame_ids"]:
                native = Path("native") / f"{camera}_{i:06d}.png"
                im.save(output / native)
                row["native_rgb_path"] = str(native)
            else:
                row["native_rgb_path"] = None
            im.thumbnail((384, 216))
            thumbnail = Path("thumbnails") / f"{camera}_{i:06d}.jpg"
            im.save(output / thumbnail, quality=94)
            row["thumbnail_path"] = str(thumbnail)
            tiles.append((i, im.copy()))
            rows.append(row)
            count += 1
            if len(tiles) == 25:
                sheet = Image.new("RGB", (1920, 1200), "#ffffff")
                draw = ImageDraw.Draw(sheet)
                for j, (fid, tile) in enumerate(tiles):
                    x, y = (j % 5) * 384, (j // 5) * 240
                    sheet.paste(tile, (x, y))
                    draw.text((x + 5, y + 220), f"{camera} #{fid:03d}  t={120 + (fid+.5)/20:.3f}s UNREVIEWED", fill="black")
                page = i // 25
                sheet.save(output / "contact" / f"{camera}_{page:02d}_{i-24:03d}-{i:03d}.jpg", quality=94)
                tiles.clear()
        if count != 100:
            raise ValueError("RGB decoder did not yield all 100 frames")
        videos[camera] = {"decoded_frames": count, "elapsed_s": time.perf_counter() - camera_started,
                          "all_pts_verified": True, "native_images": len(freeze["native_frame_ids"])}
        print(json.dumps({camera: videos[camera]}), flush=True)
    for name, signature in freeze["input_sha256"].items():
        if digest(name) != signature:
            raise ValueError(f"Frozen input changed during audit: {name}")
    write_json(output / "frames.json", rows)
    with (output / "frames.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {"schema": "nrel-dynamic-video-audit.v1", "status": "DECODE_VERIFIED_REVIEW_PENDING",
               "camera_frame_rows": len(rows), "target_time_count": 100, "videos": videos,
               "rgb_decode_passes_per_camera": 1, "contact_sheet_count": 12,
               "thumbnail_count": 300, "native_png_count": 21,
               "identity_reviewed_frames": 0, "structural_tip_constraints_established": 0,
               "d_outputs": 0, "projection_run": False, "fitting_run": False,
               "native_selection_basis": "parent request 40..46 for RGB review; not metric fit selection",
               "historical_use": freeze["historical_video_use"],
               "elapsed_worker_s": time.perf_counter() - started,
               "input_unchanged_after_run": True,
               "limitations": ["Thumbnails and full decoding do not establish B1 identity or visibility",
                               "All review fields remain unreviewed; no automatic trusted masks",
                               "Visible surface tip is distinct from structural point",
                               "No plane, structural-tip mapping, d or new blind-test claim"]}
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def launch(protocol, output):
    protocol, output = protocol.resolve(), output.resolve()
    value = json.loads(protocol.read_text())
    if value["status"] != "FROZEN_PREFLIGHT_SCOPE" or value["run_id"] != "20261004-a01":
        raise ValueError("Expected frozen DY0 protocol")
    input_dir = Path(value["input_root"]).resolve()
    if output.exists() and any(not (p.is_dir() and p.name.startswith("failed-launch-"))
                               for p in output.iterdir()):
        raise FileExistsError("Use a fresh audit output; historical or partial runs are not overwritten")
    output.mkdir(parents=True, exist_ok=True)
    paths = [Path(__file__).resolve(), protocol] + [input_dir / name for name in sorted(INPUT_FILES)]
    freeze = {"schema": "nrel-dynamic-audit-launch.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
              "input_root": str(input_dir), "input_sha256": {str(p): digest(p) for p in paths},
              "native_frame_ids": list(range(40, 47)), "contact_page_size": 25,
              "historical_video_use": value["historical_video_use"],
              "source_script": str(Path(__file__).resolve()), "python": sys.executable,
              "scope": "decode/provenance/review material only; no estimator or scorer"}
    write_json(output / "launch_freeze.json", freeze)
    runtime = Path(sys.executable).resolve().parents[1]
    policy = sandbox_policy(paths, output, runtime)
    (output / "isolation.sb").write_text(policy)
    args = ["/usr/bin/sandbox-exec", "-f", str(output / "isolation.sb"),
            sys.executable, "-B", str(Path(__file__).resolve()), "worker", "--output", str(output)]
    write_json(output / "launch_command.json", {"argv": args, "policy_sha256": digest(output / "isolation.sb")})
    started = time.perf_counter()
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    with (output / "run.log").open("w") as log:
        result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, env=environment)
    write_json(output / "launch_result.json", {"returncode": result.returncode,
                                               "elapsed_s": time.perf_counter() - started})
    if result.returncode:
        raise RuntimeError(f"Sandboxed audit failed; inspect {output / 'run.log'}")
    print(output / "summary.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("launch", "worker"))
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "launch":
        if args.protocol is None:
            parser.error("launch requires --protocol")
        launch(args.protocol, args.output)
    else:
        worker(args.output.resolve())


if __name__ == "__main__":
    main()
