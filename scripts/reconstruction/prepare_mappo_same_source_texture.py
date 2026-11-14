#!/usr/bin/env python3
"""Derive MAPPO appearance atlases from frozen RGB/masks and saved geometry.

This does not run a geometry optimizer or FAST.Farm. Every camera is an explicit
per-frame MODEL -> CV transform. Historical diagnostics and source hashes are
retained. Run with blade_recon/.venv-trial/bin/python (numpy, cv2 and scipy).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
DEFAULT_SOURCE = ROOT / "outputs/blade-recon-mappo-two-camera-20261004-a02"
DEFAULT_OUTPUT = ROOT / "outputs/mappo-same-source-texture-20261007-a01/data"
DEFAULT_ASSET = ROOT / "blender_frontend/wfrl_blender/assets/blade_recon_mappo_tex"
CAMERA_NAMES = ["T1Down", "NacelleT1"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path, raw=None):
    path = Path(path)
    raw = path.read_bytes() if raw is None else raw
    return {"source_path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw)}


def read_json(path, *, identities=None):
    path = Path(path)
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
        json.dumps(value, allow_nan=False)
    except (ValueError, OSError) as exc:
        raise ValueError(f'{path}: {exc}') from exc
    if identities is not None:
        identities[path] = record(path, raw)
    return value


def write_json(path, value):
    from wfrl_blender import atomic_json
    atomic_json.write_json(path, value, mkdir=True)


def preserve_json(path, value):
    """Keep an existing diagnostic's bytes; differing evidence needs a new output."""
    path = Path(path)
    if path.exists():
        if read_json(path) != value:
            raise ValueError('existing diagnostic differs; use a new output: ' + str(path))
        return
    write_json(path, value)


def preserve_copy(source, target, hardlink=False, *, expected_sha256=None):
    """A rerun accepts only identical prepared files; never rewrites a source."""
    source, target = Path(source), Path(target)
    expected_sha256 = digest(source) if expected_sha256 is None else expected_sha256
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if expected_sha256 != digest(target):
            raise ValueError("existing derived file differs: " + str(target))
        return "existing_identical"
    from wfrl.camera_video.publication import commit_exclusive
    with tempfile.TemporaryDirectory(prefix='.mappo-copy-', dir=target.parent) as directory:
        staged = Path(directory) / target.name
        mode = 'byte_copy'
        if hardlink:
            try:
                os.link(source, staged)
                mode = 'hardlink_read_only_use'
            except OSError:
                pass
        if not staged.exists():
            shutil.copyfile(source, staged)
        if digest(staged) != expected_sha256:
            raise ValueError(f'Source identity changed while copying: {source}')
        commit_exclusive(staged, target)
    return mode


def _publish_files(files):
    """Publish a validated file set; roll back only entries created by this call."""
    from wfrl.camera_video.publication import publish
    pending = []
    for target, source in files.items():
        if os.path.lexists(target):
            if not target.is_file() or digest(target) != digest(source):
                raise ValueError('existing derived file differs: ' + str(target))
        else:
            pending.append((target, source))
    completion_order = {'input_bundle.json': 1, 'recon.json': 2, 'source_integrity.json': 3,
                        'manifest.json': 4, 'asset_manifest.json': 4}
    pending.sort(key=lambda item: completion_order.get(item[0].name, 0))
    created, directories = [], []
    try:
        for target, source in pending:
            parents = []
            parent = target.parent
            while not parent.exists():
                parents.append(parent)
                parent = parent.parent
            for parent in reversed(parents):
                parent.mkdir()
                directories.append(parent)
            publish(source, target)
            created.append((target, target.stat()))
    except BaseException:
        for target, identity in reversed(created):
            current = target.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                target.unlink()
        for directory in reversed(directories):
            directory.rmdir()
        raise


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    raw = Path(path).read_bytes()
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    module._source_identity = record(path, raw)
    return module


def compatibility(source_recon, source_model, texture_model, vendor_model, atlas_path, dr, cols):
    modules = [load_module("_mappo_model_" + name, path) for name, path in
               (("source", source_model), ("texture", texture_model), ("vendor", vendor_model))]
    rotors = [m.Rotor(m.TurbineConfig.from_dict(source_recon["turbine"])) for m in modules]
    if any(r.cfg.to_dict() != source_recon["turbine"] for r in rotors):
        raise ValueError("model configuration differs from saved analytic model")
    if any(m.STATE_NAMES != source_recon["state_names"] for m in modules):
        raise ValueError("state order differs between source/texture/vendor")
    details = []
    for label, rotor in zip(("texture", "vendor"), rotors[1:]):
        checks = {}
        for key in ("r", "foils", "chord", "twist"):
            checks[key + "_bit_identical"] = np.array_equal(getattr(rotors[0].tpl, key), getattr(rotor.tpl, key))
        checks["faces_bit_identical"] = np.array_equal(rotors[0].tpl.faces(), rotor.tpl.faces())
        vertices_equal, axes_equal, vmax, amax = True, True, 0.0, 0.0
        for frame in source_recon["frames"]:
            state = np.asarray(frame["state"], float)
            vertices, axes = rotors[0].forward(state)
            actual_v, actual_a = rotor.forward(state)
            vertices_equal &= np.array_equal(vertices, actual_v)
            axes_equal &= np.array_equal(axes, actual_a)
            vmax = max(vmax, float(np.max(np.abs(vertices - actual_v))))
            amax = max(amax, float(np.max(np.abs(axes - actual_a))))
        checks.update(vertices_bit_identical=bool(vertices_equal), axes_bit_identical=bool(axes_equal),
                      maximum_vertex_difference_m=vmax, maximum_axis_difference_m=amax,
                      samples_checked=len(source_recon["frames"]), compared_to="original solver model")
        if not all(v for k, v in checks.items() if k.endswith("_bit_identical")):
            raise ValueError(label + " model differs numerically from saved geometry")
        details.append({"model": label, **checks})
    atlas_module = load_module("_mappo_atlas", atlas_path)
    atlases = [atlas_module.Atlas(r.tpl, dr=dr, n_cols=cols) for r in rotors]
    uv = [a.corner_uv(r.tpl.faces()).astype(np.float32) for a, r in zip(atlases, rotors)]
    if not all(np.array_equal(uv[0], u) for u in uv[1:]):
        raise ValueError("texture and vendor corner UV contracts differ")
    return {"configuration": "legacy analytic model; no healthy_template declared",
            "source_model": modules[0]._source_identity, "texture_model": modules[1]._source_identity,
            "vendor_model": modules[2]._source_identity, "models": details,
            "uv_float32_bit_identical": True, "atlas": atlases[0].meta()}


def _prepare(args, final_output):
    source, out = args.source.resolve(), args.output.resolve()
    inp = source / "input"
    identities = {}
    original = read_json(source / "reconstruction/recon.json", identities=identities)
    calibration = read_json(inp / "calibration.json", identities=identities)
    solver = read_json(source / "reconstruction/solver_manifest.json", identities=identities)
    capture = read_json(inp / "capture_report.json", identities=identities)
    if len(original["frames"]) != 601 or len(calibration["frames"]) != 601:
        raise ValueError("this delivery requires the complete 601-frame source")
    if original["turbine"] != calibration["turbine"] or capture["model_config"] != original["turbine"]:
        raise ValueError("source model configuration differs across source records")
    if identities[inp / "calibration.json"]['sha256'] != solver["calibration_sha256"]:
        raise ValueError(f'{inp / "calibration.json"}: dynamic calibration identity changed')
    original_model = ROOT / "blade_recon/model.py"
    if digest(original_model) != solver["algorithm_sources"]["model.py"]["sha256"]:
        raise ValueError("relocated original solver model hash differs")
    vendor_model = ROOT / "blender_frontend/wfrl_blender/_vendor/blade_recon/model.py"
    engine = out / "engine/blade_recon0.2"
    legacy = out / "engine/blade_recon"
    for path in sorted((ROOT / "blade_recon0.2").glob("*.py")):
        preserve_copy(path, engine / path.name)
    for name in ("input_bundle.py", "model.py", "camera.py"):
        preserve_copy(ROOT / "blade_recon" / name, legacy / name)
    checks = compatibility(original, original_model, engine / "model.py", vendor_model,
                           engine / "atlas.py", args.atlas_dr, args.atlas_cols)
    if checks['source_model']['sha256'] != solver['algorithm_sources']['model.py']['sha256']:
        raise ValueError(f'{original_model}: loaded solver model hash differs')
    checks['texture_model']['source_path'] = str(final_output / 'engine/blade_recon0.2/model.py')
    write_json(out / "model_compatibility.json", checks)
    derived = copy.deepcopy(original)
    input_root = out / "input"
    frames, source_files, access_log = [], {}, []
    orthogonal_max = 0.0
    copy_modes = set()
    for k, (saved, cal, new) in enumerate(zip(original["frames"], calibration["frames"], derived["frames"])):
        for key, expected in (("frame", k), ("t", k / 10), ("sim_t", 117 + k / 10), ("blender_frame", 1 + 6 * k)):
            if abs(saved[key] - expected) > 1e-8 or abs(cal[key] - expected) > 1e-8:
                raise ValueError("source clock mismatch at " + str(k))
        if not np.array_equal(saved["T_world_from_model"], cal["T_world_from_model"]):
            raise ValueError("source model transform mismatch")
        if [c["camera"] for c in saved["cameras"]] != CAMERA_NAMES or [c["name"] for c in cal["cameras"]] != CAMERA_NAMES:
            raise ValueError("source camera identities/order mismatch")
        new["source_camera_diagnostics"] = copy.deepcopy(saved["cameras"])
        new["cameras"] = []
        cc = []
        for old_camera, camera in zip(saved["cameras"], cal["cameras"]):
            transform = np.asarray(camera["T_cv_from_world"], float)
            orthogonal_max = max(orthogonal_max, float(np.max(np.abs(transform[:3, :3] @ transform[:3, :3].T - np.eye(3)))))
            mapped = {key: copy.deepcopy(camera[key]) for key in ("name", "W", "H", "K")}
            # Historical key says "world", but calibration/source documentation
            # explicitly identifies these matrices as MODEL -> CV. No multiply,
            # static approximation, orthogonalization or field inference occurs.
            mapped["T_cv_from_model"] = copy.deepcopy(camera["T_cv_from_world"])
            new["cameras"].append(copy.deepcopy(mapped))
            for field, role, prefix, hash_key in (("rgb", "RGB", camera["name"], "rgb_sha256"),
                    ("mask", "RGB_derived_mask", "masks/" + camera["name"], "mask_sha256")):
                relative = prefix + "/%06d.png" % k
                path = inp / relative
                raw = path.read_bytes()
                identity = record(path, raw)
                if identity["sha256"] != old_camera[hash_key]:
                    raise ValueError("saved RGB/mask hash mismatch: " + str(path))
                try:
                    mode = cv2.IMREAD_COLOR if field == 'rgb' else cv2.IMREAD_GRAYSCALE
                    image = cv2.imdecode(np.frombuffer(raw, np.uint8), mode)
                except Exception as exc:
                    raise ValueError(f'{path}: cannot decode image: {exc}') from exc
                expected_shape = (camera['H'], camera['W'], 3) if field == 'rgb' else (camera['H'], camera['W'])
                if image is None or image.shape != expected_shape:
                    raise ValueError(f'{path}: image dimensions/channels differ from calibration')
                copy_modes.add(preserve_copy(path, input_root / relative,
                                             expected_sha256=identity['sha256']))
                source_files[relative] = identity
                access_log.append({"path": relative, "role": role, "sha256": identity["sha256"], "bytes": identity["size_bytes"]})
                mapped[field] = relative
            cc.append(mapped)
        frames.append({"frame": k, "t": k / 10, "cameras": cc})
        for key in ("state", "observed_sections", "t", "sim_t", "blender_frame", "T_world_from_model"):
            if saved[key] != new[key]:
                raise ValueError("derived data changed saved geometry/time: " + key)
    manifest = {"schema": "blade-surface-input.v1", "extrinsic_from_frame": "model",
        "pixel_coordinates": "original_image_pixel_centers", "color_order": "RGB",
        "turbine": original["turbine"], "frames": frames,
        "provenance": {"purpose": "same-source appearance postprocess; saved geometry unchanged",
                       "source_run": str(source), "calibration": identities[inp / "calibration.json"],
                       "extrinsics": "historical T_cv_from_world is documented MODEL->CV; renamed without modifying numeric values"}}
    write_json(input_root / "input_bundle.json", manifest)
    access_log.insert(0, {"path": "input_bundle.json", "role": "input_manifest",
        "sha256": digest(input_root / "input_bundle.json"), "bytes": (input_root / "input_bundle.json").stat().st_size})
    derived["input_access_log"] = access_log
    derived["source"]["same_source_texture_adaptation"] = {"input_bundle_sha256": digest(input_root / "input_bundle.json"),
        "original_recon_sha256": identities[source / "reconstruction/recon.json"]['sha256'],
        "policy": "original states/time/coverage untouched; cameras normalized from original dynamic calibration; diagnostics retained"}
    write_json(out / "recon.json", derived)
    identities[source / 'REPORT.md'] = record(source / 'REPORT.md')
    sources = {str(p.relative_to(source)): identity for p, identity in identities.items()}
    report = {"status": "PREPARED", "source_run": str(source), "source_files": sources,
        "input_files": source_files, "input_file_count": len(source_files), "input_copy_modes": sorted(copy_modes),
        "original_source_paths_relocated": "original solver root missing; repository blade_recon/model.py verified with original declared SHA256",
        "original_states_and_observed_sections_preserved": True, "original_time_and_model_transforms_preserved": True,
        "cameras": CAMERA_NAMES, "samples": 601, "dynamic_camera_matrices": 1202,
        "dynamic_transform_values_preserved": True, "rotation_orthogonality_max_absolute_error": orthogonal_max,
        "model_compatibility": checks, "geometry_precision_status": "NOT_ACCEPTED",
        "source_capture": capture, "solver_rerun": False, "fastfarm_rerun": False}
    write_json(out / "source_integrity.json", report)
    return out, engine


def prepare(args):
    final_output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix='mappo-prepare-') as directory:
        staged_args = copy.copy(args)
        staged_args.output = Path(directory) / 'data'
        try:
            staged, _ = _prepare(staged_args, final_output)
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f'{args.source}: invalid source fields: {exc}') from exc
        _publish_files({final_output / p.relative_to(staged): p
                        for p in staged.rglob('*') if p.is_file()})
    return final_output, final_output / 'engine/blade_recon0.2'


def _package(args, out, report_output):
    asset = args.asset.resolve()
    identities = {}
    texture = read_json(out / "texture/texture.json", identities=identities)
    data_path = out / "texture/texture_data.npz"
    try:
        with np.load(data_path, allow_pickle=False) as archive:
            data = {name: archive[name] for name in ('has', 'nview', 'val', 'raw', 'wmax')}
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f'{data_path}: {exc}') from exc
    stats = []
    images, image_identities = [], {}
    atlas = texture["atlas"]
    for key, array in data.items():
        if array.shape != (3, atlas['H'], atlas['W']):
            raise ValueError(f'{data_path}: array shape differs from atlas: {key}')
    for b in range(3):
        path = out / 'texture' / ('blade%d_tex.png' % (b + 1))
        raw = path.read_bytes()
        png = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        if png is None or png.shape != (atlas["H"], atlas["W"], 4):
            raise ValueError(f'{path}: texture PNG shape/channel mismatch')
        images.append(png)
        image_identities[path.name] = record(path, raw)['sha256']
        known = png[..., 3] > 0
        if not np.array_equal(known, data["has"][b]):
            raise ValueError("PNG unknown alpha disagrees with retained sampling coverage")
        brightness = png[..., 0][known]
        stats.append({"blade": b + 1, "covered_texels": int(known.sum()), "total_texels": int(known.size),
            "whole_surface_coverage": float(known.mean()), "normalized_gray_min": int(brightness.min()) if known.any() else None,
            "normalized_gray_max": int(brightness.max()) if known.any() else None,
            "normalized_gray_median": float(np.median(brightness)) if known.any() else None,
            "source_sample_count_max": int(data["nview"][b].max()), "unobserved_texels_alpha_zero": True})
    integrity = read_json(out / "source_integrity.json", identities=identities)
    reconstruction = read_json(out / 'recon.json', identities=identities)
    input_bundle = read_json(out / 'input/input_bundle.json', identities=identities)
    contribution_path = out / "sampling_diagnostics.json"
    contributions = read_json(contribution_path) if contribution_path.exists() else None
    identity_frames = texture["input_contract"]["frames"]
    if len(identity_frames) != 601 or not all(f["saved_calibration_checked"] for f in identity_frames):
        raise ValueError("texture did not validate all saved per-frame calibrations")
    if len(reconstruction['frames']) != 601 or len(input_bundle['frames']) != 601:
        raise ValueError(f'{out}: incomplete reconstruction/input frame correspondence')
    for k, (identity, saved, source) in enumerate(zip(identity_frames, reconstruction['frames'], input_bundle['frames'])):
        if (any(row.get('frame') != k or row.get('t') != k / 10 for row in (identity, saved, source))
                or identity.get('cameras') != CAMERA_NAMES
                or any([camera.get('name') for camera in row['cameras']] != CAMERA_NAMES for row in (saved, source))):
            raise ValueError(f'{out}: texture/input/reconstruction frame or clock mismatch at {k}')
    if not texture["input_contract"]["reconstruction_input_provenance_checked"]:
        raise ValueError("texture input hashes were not checked")
    quality = {"status": "GENERATED_SAME_SOURCE", "blades": stats,
        "geometry_precision_status": "NOT_ACCEPTED", "color_policy": "RGB converted to gray by unchanged texture engine; original render is Workbench material color",
        "temporal_policy": "one fixed appearance atlas per blade from 601 timestamps; geometry moves/deforms at saved 10Hz samples",
        "unknown_policy": "never sampled texels retain alpha=0; no invented fill", "fusion": texture["params"],
        "visual_inspection": {"status": "NOT_REVIEWED",
            "atlas_sha256": image_identities,
            "source_integrity_sha256": identities[out / "source_integrity.json"]['sha256']},
        "limits": ["rendered simulation images, not field camera measurements", "geometry mismatch and incomplete viewpoints can misplace appearance", "normalized gray appearance is not calibrated material reflectance", "no independent same-source texture truth or field-quality acceptance"]}
    if contributions is not None:
        quality["sampling_diagnostics"] = contributions
    for key in ("val", "raw", "wmax"):
        if not np.isfinite(data[key]).all():
            raise ValueError("nonfinite output appearance/weight data: " + key)
    quality["appearance_and_weights_finite"] = True
    inspection = []
    # Select a supported image patch for viewing only. This measures atlas
    # contrast and alpha; it does not detect or assert any surface defect.
    for b in range(3):
        png = images[b]
        known = png[..., 3] > 0
        gray = png[..., 0].astype(np.float32)
        cover = cv2.boxFilter(known.astype(np.float32), -1, (19, 19))
        mean = cv2.boxFilter(gray, -1, (19, 19))
        variance = np.maximum(cv2.boxFilter(gray * gray, -1, (19, 19)) - mean * mean, 0)
        rows = (np.arange(atlas["H"]) + 0.5) * atlas["dr"] + atlas["r0"]
        for label, low, high, min_coverage, rank in (("同源中段观察点", 45, 55, 0.999, 0),
                ("同源叶尖拼接观察点", 55, 60, 0.98, 1)):
            eligible = known & (cover > min_coverage) & (rows[:, None] >= low) & (rows[:, None] < high)
            if eligible.any():
                score = np.where(eligible, variance, -1)
                row, col = np.unravel_index(np.argmax(score), score.shape)
                inspection.append({"label": label, "blade": b + 1,
                    "radius_m": float(rows[row]), "tau": float((col + 0.5) / atlas["W"]),
                    "size_m": 2.0, "patch_alpha_coverage": float(cover[row, col]),
                    "patch_gray_std": float(np.sqrt(variance[row, col])), "display_rank": rank,
                    "source": "supported atlas patch chosen for viewing; no defect inference"})
    inspection.sort(key=lambda p: (p["display_rank"], -p["patch_gray_std"]))
    quality["inspection_points"] = inspection
    preserve_json(report_output / "texture_quality.json", quality)
    files = ["recon.json", "texture/texture.json"] + ["texture/blade%d_tex.png" % b for b in (1, 2, 3)]
    file_sources = {name: out / name for name in files}
    for name in files:
        expected = image_identities.get(Path(name).name) or identities[out / name]['sha256']
        preserve_copy(out / name, asset / name, expected_sha256=expected)
    for name, source in [("model.py", out / "engine/blade_recon0.2/model.py"), ("atlas.py", out / "engine/blade_recon0.2/atlas.py")]:
        preserve_copy(source, asset / "reference" / name)
        files.append("reference/" + name)
        file_sources["reference/" + name] = source
    for name in ("model_compatibility.json", "texture_quality.json"):
        source = report_output / name if name == 'texture_quality.json' else out / name
        preserve_copy(source, asset / name)
        files.append(name)
        file_sources[name] = out / name
    manifest = {"schema": "wfrl.blade-recon-provenance.v1", "source_classification": "SAME_SOURCE_SIMULATION",
        "source_label": "MAPPO同源模拟图像纹理 · T1两相机 · 601样本 · 精度NOT_ACCEPTED",
        "description": "Offline appearance fusion from the original MAPPO two-camera RGB/masks and unchanged 601 saved reconstruction states.",
        "files": {name: {**record(asset / name), "source_path": str(file_sources[name])} for name in files},
        "model": {"bundled_path": "../../_vendor/blade_recon/model.py", "sha256": integrity["model_compatibility"]["vendor_model"]["sha256"],
                  "policy": "bit-identical to original source and texture forward model on every saved state"},
        "source_model": integrity["model_compatibility"]["source_model"], "texture_model": integrity["model_compatibility"]["texture_model"],
        "model_compatibility": integrity["model_compatibility"], "source_hashes": integrity["source_files"],
        "source_package_hashes": integrity["source_capture"]["source_hashes"],
        "mappo_manifest_sha256": integrity["source_capture"]["source_hashes"]["assets/mappo/manifest.json"],
        "input_bundle": identities[out / "input/input_bundle.json"], "source_integrity": identities[out / "source_integrity.json"],
        "clock": {"fps": 10.0, "samples": 601, "source_frame_start": 0, "source_frame_end": 600,
                  "time_start_s": 0.0, "time_end_s": 60.0, "simulation_start_s": 117.0, "simulation_end_s": 177.0,
                  "source_hz": 40.0, "timeline_fps": 60.0, "frame_start": 1, "frame_end": 3601, "stride": 6,
                  "relationship_to_mappo": "same original simulated physical timestamps; t = simulation time - 117"},
        "texture_policy": quality, "geometry_precision_status": "NOT_ACCEPTED",
        "legend": "green/orange denote silhouette constraint support; both colors are algorithm estimates", "inspection_points": inspection}
    preserve_json(asset / "manifest.json", manifest)
    preserve_json(report_output / "asset_manifest.json", manifest)
    return stats, len(files)


def package(args, out):
    with tempfile.TemporaryDirectory(prefix='mappo-package-') as directory:
        staged = Path(directory)
        staged_args = copy.copy(args)
        staged_args.asset = staged / 'asset'
        reports = staged / 'reports'
        stats, count = _package(staged_args, out, reports)
        files = {args.asset.resolve() / p.relative_to(staged_args.asset): p
                 for p in staged_args.asset.rglob('*') if p.is_file()}
        files.update({out / p.name: p for p in reports.iterdir()})
        _publish_files(files)
    print(json.dumps({'asset': str(args.asset.resolve()), 'quality': stats, 'files': count}, ensure_ascii=False), flush=True)


def write_diagnostic_runner(out, engine):
    """Record participation and reject invalid projected samples, without edits
    to the pinned texture algorithm or any change to its returned arrays.
    """
    from wfrl_blender import atomic_json
    runner = out / "texture_diagnostic_runner.py"
    script = (f'import sys\nsys.path.insert(0, {str(ROOT / "blender_frontend")!r})\n'
              'from wfrl_blender import atomic_json\n') + '''import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent / "engine/blade_recon0.2"))
import texture as T
current = {}
frames = []
checks = {"sample_view_calls": 0, "nonfinite_projected_texels": 0,
          "nonfinite_projected_texels_with_positive_weight": 0,
          "returned_gray_and_weights_finite": True}
validate = T.validate_saved_cameras
sample = T.sample_view
frame_views = T.frame_views
def checked_calibration(frame, cameras):
    validate(frame, cameras)
    current.update(frame=frame["frame"], t=frame["t"])
def checked_sample(cam, img, obs_mask, R, V, nrm, b, atlas, rows, row_ok, omega, exposure):
    uv, z = cam.project(T.triangulated_atlas_surface(atlas, V[b], rows))
    invalid = ~np.isfinite(uv).all(-1) | ~np.isfinite(z)
    checks["sample_view_calls"] += 1
    checks["nonfinite_projected_texels"] += int(invalid.sum())
    result = sample(cam, img, obs_mask, R, V, nrm, b, atlas, rows, row_ok, omega, exposure)
    if result is not None:
        g, w, rs, rc = result
        if not np.isfinite(g).all() or not np.isfinite(w).all() or (w < 0).any():
            raise ValueError("sample_view returned nonfinite gray/weight or negative weight")
        bad = int((invalid & (w > 0)).sum())
        checks["nonfinite_projected_texels_with_positive_weight"] += bad
        if bad:
            raise ValueError("invalid projection has positive texture fusion weight")
    return result
def checked_views(*args, **kwargs):
    result = frame_views(*args, **kwargs)
    frames.append({**current, "views": [{"camera": v[0], "blade": v[1] + 1,
        "span_rows": [v[2].start, v[2].stop], "accepted_texels": int((v[4] > 0).sum())} for v in result]})
    return result
T.validate_saved_cameras = checked_calibration
T.sample_view = checked_sample
T.frame_views = checked_views
T.main()
views = [v for f in frames for v in f["views"]]
diagnostics = {"frames_checked_by_texture_input_contract": 601,
    "frames_attempted_for_appearance": len(frames),
    "frames_contributing_appearance": sum(bool(f["views"]) for f in frames),
    "total_contributing_views": len(views), "frames": frames,
    "views_by_camera": {c: sum(v["camera"] == c for v in views) for c in ("T1Down", "NacelleT1")},
    "views_by_blade": {str(b): sum(v["blade"] == b for v in views) for b in (1, 2, 3)},
    "projection_checks": checks,
    "warning_explanation": "existing sample_view rounds/casts invalid UV before masking; invalid UV are excluded and zero-weighted; warnings retained in run log"}
atomic_json.write_text(Path(__file__).with_name("sampling_diagnostics.json"), json.dumps(diagnostics, indent=2) + "\\n")
'''
    atomic_json.write_text(runner, script)
    return runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--asset", type=Path, default=DEFAULT_ASSET)
    parser.add_argument("--atlas-dr", type=float, default=0.05)
    parser.add_argument("--atlas-cols", type=int, default=384)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    if not np.isfinite(args.atlas_dr) or args.atlas_dr <= 0 or args.atlas_cols <= 0:
        parser.error('atlas-dr must be finite and positive; atlas-cols must be positive')
    started = time.time()
    if args.package_only:
        package(args, args.output.resolve())
        return
    out, engine = prepare(args)
    print("601 source states/cameras/hashes and all model vertices/axes/UV verified", flush=True)
    if args.prepare_only:
        return
    runner = write_diagnostic_runner(out, engine)
    with tempfile.TemporaryDirectory(prefix='mappo-texture-') as directory:
        staged = Path(directory) / 'texture'
        command = [sys.executable, str(runner), "--data", str(out / "input"),
            "--recon", str(out / "recon.json"), "--out", str(staged),
            "--atlas-dr", str(args.atlas_dr), "--atlas-cols", str(args.atlas_cols), "--fusion", "best"]
        write_json(out / "generation_command.json", {"argv": command, "python": sys.executable,
            "purpose": "saved-state texture postprocess only", "engine_sources": {p.name: record(p) for p in engine.glob("*.py")}})
        subprocess.run(command, cwd=ROOT, check=True)
        _publish_files({out / 'texture' / p.relative_to(staged): p for p in staged.rglob('*') if p.is_file()})
    package(args, out)
    print("same-source texture delivery completed in %.1f s" % (time.time() - started), flush=True)


if __name__ == "__main__":
    main()
