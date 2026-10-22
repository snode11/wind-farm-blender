"""Strict estimate-only review reader; import directly without the WFRL package.

This module reads signed frozen estimates and original video bytes.  It does
not import Torch, Blender, replay geometry, flexible states or scoring data.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

GROUPS = ("G1", "G2", "G3")
FRAMES = (42, 43, 44, 45)
POINT_SCOPE = "PRIOR_DEFINED_STRUCTURAL_POINT"
RANGE_SCOPE = "independent_per_frame_image_equivalent_grid_not_joint_posterior"
SAMPLING_KEYS = {"start_s", "end_s_exclusive", "fps", "frame_count_per_camera", "simulation_time", "pts"}


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _array(value, shape, name):
    try:
        result = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid " + name) from exc
    if result.shape != shape or result.dtype.kind not in "fiu" or not np.isfinite(result).all():
        raise ValueError("Invalid finite shape for " + name)
    return result.astype(float)


def _signature(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def sample_time(frame_id):
    return 120.025 + frame_id / 20


class ReviewPackage:
    """All-or-nothing immutable package; exact saved frames, no interpolation."""

    def __init__(self, path, manifest, sequence, geometry):
        self.path = path
        self.manifest = manifest
        self.sequence = sequence
        self.geometry = geometry
        self.manifest_sha256 = sha256(path / "manifest.json")
        self.point_scope = POINT_SCOPE
        self.records = {group: {r["frame_id"]: r for r in sequence["groups"][group]["records"]} for group in GROUPS}

    @classmethod
    def load(cls, directory):
        root = Path(directory).expanduser().resolve()
        initial_manifest_sha256 = sha256(root / "manifest.json")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("schema") != "nrel-dynamic-review-package.v1"
                or manifest.get("target") != "T1/B1" or manifest.get("status") != "REVIEW_ONLY"
                or manifest.get("units") != "m"
                or manifest.get("condition") != "IDEAL_SHARED_RIGID_PLANE_ASSISTANCE"):
            raise ValueError("Review package identity, units or conditional status mismatch")
        if manifest.get("point_scope") != POINT_SCOPE:
            raise ValueError("Structural-point scope mismatch")
        sampling = manifest.get("sampling")
        if (not isinstance(sampling, dict) or set(sampling) != SAMPLING_KEYS
                or any(not _number(sampling.get(k)) for k in ("start_s", "end_s_exclusive", "fps", "frame_count_per_camera"))
                or sampling["start_s"] != 120 or sampling["end_s_exclusive"] != 125
                or sampling["fps"] != 20 or type(sampling["frame_count_per_camera"]) is not int
                or sampling["frame_count_per_camera"] != 100
                or sampling["simulation_time"] != "start+(n+.5)/fps" or sampling["pts"] != "n/fps"):
            raise ValueError("Original video sampling mismatch")
        files = manifest.get("files")
        if not isinstance(files, dict) or not {"sequence.json", "geometry.npz"}.issubset(files):
            raise ValueError("Missing signed estimate files")
        for name, expected in files.items():
            if (not isinstance(name, str) or Path(name).name != name or name in ("", ".", "..", "manifest.json")
                    or not _signature(expected) or (root / name).resolve().parent != root):
                raise ValueError("Unsafe review file path or signature")
            if sha256(root / name) != expected:
                raise ValueError("Estimate file hash mismatch: " + name)
        videos = manifest.get("videos")
        if not isinstance(videos, dict) or set(videos) != {"C1", "C2", "C3"}:
            raise ValueError("Exactly three signed original videos are required")
        for camera, entry in videos.items():
            if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                    or not Path(entry["path"]).is_absolute() or not _signature(entry.get("sha256"))):
                raise ValueError("Invalid original video binding: " + camera)
            if sha256(entry["path"]) != entry["sha256"]:
                raise ValueError("Original video hash mismatch: " + camera)
        sequence = json.loads((root / "sequence.json").read_text(encoding="utf-8"))
        if (sequence.get("schema") != "nrel-dynamic-review-sequence.v1"
                or sequence.get("target") != "T1/B1" or sequence.get("frames") != list(FRAMES)
                or any(type(f) is not int for f in sequence["frames"])
                or not isinstance(sequence.get("groups"), dict) or set(sequence["groups"]) != set(GROUPS)):
            raise ValueError("Sequence identity or exact saved-frame contract mismatch")
        for group in GROUPS:
            records = sequence["groups"][group].get("records")
            if not isinstance(records, list) or [r.get("frame_id") for r in records] != list(FRAMES):
                raise ValueError("Incomplete, reordered or duplicated group records: " + group)
            for record in records:
                cls._validate_record(record)
        with np.load(root / "geometry.npz", allow_pickle=False) as archive:
            expected_keys = {"faces", "material_ids", *(g + "_vertices_world" for g in GROUPS)}
            if set(archive.files) != expected_keys:
                raise ValueError("Unexpected estimate geometry arrays")
            geometry = {key: archive[key].copy() for key in archive.files}
        ids = geometry["material_ids"]
        if (ids.ndim != 2 or ids.shape[1] != 2 or len(ids) < 3 or ids.dtype.kind not in "iu"
                or (ids < 0).any() or len(np.unique(ids, axis=0)) != len(ids)):
            raise ValueError("Invalid or duplicated material identity")
        faces = geometry["faces"]
        if (faces.ndim != 2 or faces.shape[1] != 3 or len(faces) == 0 or faces.dtype.kind not in "iu"
                or (faces < 0).any() or (faces >= len(ids)).any()
                or any(len(set(face)) != 3 for face in faces.tolist())):
            raise ValueError("Invalid triangle topology")
        for group in GROUPS:
            vertices = geometry[group + "_vertices_world"]
            if (vertices.shape != (4, len(ids), 3) or vertices.dtype.kind not in "fiu"
                    or not np.isfinite(vertices).all()):
                raise ValueError("Invalid finite group surface: " + group)
            triangles = vertices[:, faces]
            cross = np.cross(triangles[:, :, 1] - triangles[:, :, 0], triangles[:, :, 2] - triangles[:, :, 0])
            if (np.linalg.norm(cross, axis=-1) <= 1e-14).any():
                raise ValueError("Degenerate estimate surface triangles: " + group)
        for array in geometry.values():
            array.setflags(write=False)
        # Detect concurrent source modifications before returning any data.
        for name, expected in files.items():
            if sha256(root / name) != expected:
                raise ValueError("Estimate input changed while loading")
        if sha256(root / "manifest.json") != initial_manifest_sha256:
            raise ValueError("Manifest changed while loading")
        return cls(root, manifest, sequence, geometry)

    @staticmethod
    def _validate_record(record):
        frame = record.get("frame_id")
        if type(frame) is not int or frame not in FRAMES or not _number(record.get("sim_time_s")) or abs(record["sim_time_s"] - sample_time(frame)) > 1e-9:
            raise ValueError("Record time does not match an original saved frame")
        if not isinstance(record.get("status"), str) or not record["status"]:
            raise ValueError("Missing explicit estimate status")
        q = _array(record.get("q_m"), (2,), "candidate q")
        if (abs(q) > .5 + 1e-12).any():
            raise ValueError("Candidate q exceeds the registered bounds")
        point = _array(record.get("point_world_m"), (3,), "prior-defined point")
        center = _array(record.get("center_world_m"), (3,), "plane center")
        normal = _array(record.get("normal_world_unit"), (3,), "plane normal")
        foot = _array(record.get("foot_world_m"), (3,), "plane foot")
        if abs(np.linalg.norm(normal) - 1) > 1e-10:
            raise ValueError("Nonunit ideal plane normal")
        d, absolute = record.get("d_m"), record.get("abs_d_m")
        if not _number(d) or not _number(absolute) or abs(absolute - abs(d)) > 1e-9:
            raise ValueError("Missing or inconsistent signed/absolute distance")
        if abs(float(normal @ (point - center)) - d) > 1e-9 or not np.allclose(foot, point - d * normal, rtol=0, atol=1e-9):
            raise ValueError("Point, plane, foot and distance disagree")
        if record.get("range_scope") != RANGE_SCOPE:
            raise ValueError("Ambiguity range must retain its independent image-grid scope")
        interval = record.get("range_m")
        if interval is not None:
            interval = _array(interval, (2,), "image-grid d range")
            if interval[0] > interval[1]:
                raise ValueError("Reversed image-grid d range")
        # Range need not contain a group's d: it describes per-frame image
        # equivalence, not the temporal fit or a joint posterior interval.

    def at_scene_frame(self, frame, group="G3"):
        if group not in GROUPS:
            raise ValueError("Unknown review group")
        if type(frame) is not int or not 1 <= frame <= 100:
            raise ValueError("Scene frame must lie in the original 1..100 movie timeline")
        index = frame - 1
        record = self.records[group].get(index)
        if record is None:
            return None
        return {"record": record, "vertices_world": self.geometry[group + "_vertices_world"][FRAMES.index(index)],
                "faces": self.geometry["faces"], "point_scope": POINT_SCOPE, "interpolated": False}
