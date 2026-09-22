"""One simulation clock for camera exposure and independently computed labels.

No Blender or solver is required. Geometry is reconstructed from the immutable
review archive; event measurements are used only as an independent audit.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import numpy as np

from wfrl.lidar.moving_tower import horizontal_clearance

CLEARANCE_DEFINITION = "tip-reference.same-global-height-moving-tower.signed-horizontal.v1"
REFERENCE_DEFINITION = "physics.tip_reference.v1: perimeter-coordinate centroid of outermost of 19 solver AeroDyn sections"
INTERPOLATION = {
    "blade": "linear deformation in moving pitched blade-root frame; linear unwrapped pose",
    "tower": "linear source station affine transforms applied to reference vertices",
    "nacelle": "linear translation; linear rotation projected to SO(3) by SVD",
    "rpm": "linear source rotor_speed_rpm",
    "clearance": "recompute horizontal triangulated-tower section distance after geometry reconstruction; never interpolate distances",
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def interpolate_transform(a, b, alpha):
    """Same SO(3) projection as frontend tower_motion.interpolate_transform."""
    out = np.asarray(a, float) * (1 - alpha) + np.asarray(b, float) * alpha
    u, _, vt = np.linalg.svd(out[..., :3])
    rotation = u @ vt
    if np.any(np.linalg.det(rotation) < 0):
        raise ValueError("Rotation interpolation crossed an improper rotation")
    out[..., :3] = rotation
    return out


def _rotation(axis, degrees):
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    return (np.array(((1, 0, 0), (0, c, -s), (0, s, c))),
            np.array(((c, 0, s), (0, 1, 0), (-s, 0, c))),
            np.array(((c, -s, 0), (s, c, 0), (0, 0, 1))))[axis]


def blade_root_frame(scalars, pose, blade, nacelle):
    """NumPy equivalent of frontend deflection.rigid_frame (pitched axes)."""
    yaw = np.asarray(nacelle)[:, :3]
    tilt = np.radians(scalars['ShftTilt'])
    hub = yaw @ np.array((scalars['OverHang'] * np.cos(tilt), 0,
                         scalars['TowerHt'] + scalars['Twr2Shft']
                         + scalars['OverHang'] * np.sin(tilt))) + nacelle[:, 3]
    axes = (yaw @ _rotation(1, -scalars['ShftTilt'])
            @ _rotation(0, pose[1] + (blade - 1) * 120)
            @ _rotation(1, scalars['PreCone(1)'])
            @ _rotation(2, -pose[blade + 2]))
    return hub, axes


class SourceGeometry:
    """Verified v3 review source. All public sampled coordinates are world metres.

    camera_mount is expressed in the *rest nacelle material frame*: include the
    static tower-top translation if starting from a YawRoot-relative camera.
    """
    def __init__(self, path, turbine_id="T1"):
        self.path = Path(path).resolve()
        self.manifest = json.loads((self.path / "manifest.json").read_text())
        m = self.manifest
        if m.get("schema") != "wfrl.farm-flex-review.v3" or m.get("status") != "REVIEW_ONLY":
            raise ValueError("Camera data requires a v3 REVIEW_ONLY source geometry archive")
        if m.get("tower_model") != "elastodyn-flexible" or not m.get("includes_rigid_motion"):
            raise ValueError("Expected source transforms including rigid and flexible motion")
        self.turbine_id = turbine_id
        self.turbine_index = m["turbine_ids"].index(turbine_id)
        self.source_hashes = {"manifest.json": sha256(self.path / "manifest.json")}
        for name, expected in m["files"].items():
            if Path(name).name != name:
                raise ValueError("Source file must be a package-local filename")
            actual = sha256(self.path / name)
            if actual != expected:
                raise ValueError("Source integrity mismatch: " + name)
            self.source_hashes[name] = actual
        required = {"geometry.npz", "tower-motion.npz", "reference-surfaces.npz", "data.json", "deflection-t1.json"}
        if not required.issubset(self.source_hashes):
            raise ValueError("Source geometry missing: " + ", ".join(sorted(required - self.source_hashes.keys())))
        with np.load(self.path / "geometry.npz", allow_pickle=False) as a:
            self.times = a["times"].astype(float)
            self.transforms = a["transforms"][:, self.turbine_index].astype(float)
            self.poses = a["poses"][:, self.turbine_index].astype(float)
        with np.load(self.path / "tower-motion.npz", allow_pickle=False) as a:
            self.tower_times = a["times"].astype(float)
            self.heights = a["heights"].astype(float)
            self.tower_transforms = a["transforms"][:, self.turbine_index].astype(float)
            self.nacelles = a["nacelle"][:, self.turbine_index].astype(float)
        with np.load(self.path / "reference-surfaces.npz", allow_pickle=False) as a:
            self.blade_reference = a["blades"].astype(float).reshape(3, 19, -1, 3)
            self.blade_triangles = a["triangles"].copy()
            self.tower_reference = a["tower"].astype(float)
            self.tower_triangles = a["tower_triangles"].copy()
        contents = json.loads((self.path / "data.json").read_text())[turbine_id]
        self.motion, self.measurements = contents["motion"], contents["measurements"]
        self.rpm = np.asarray([r["rotor_speed_rpm"] for r in self.motion], float)
        self.scalars = json.loads((self.path / "deflection-t1.json").read_text())["scalars"]
        self.layout = np.asarray(m["layout_m"][self.turbine_index], float)
        self._validate()
        # Reference surfaces have exact station heights; no display-mesh resampling.
        self.tower_station = np.argmin(abs(self.tower_reference[:, 2, None] - self.heights), axis=1)
        if not np.allclose(self.heights[self.tower_station], self.tower_reference[:, 2], atol=1e-6, rtol=0):
            raise ValueError("Reference tower vertices do not match source station heights")
        # Applying an affine transform to the perimeter centroid is exactly the
        # centroid of the transformed perimeter (without display tip taper).
        self.tip_reference = self.blade_reference[:, -1].mean(axis=1)
        tr = self.transforms[:, :, -1]
        self.source_tips = np.einsum("nbij,bj->nbi", tr[..., :3], self.tip_reference) + tr[..., 3]

    def _validate(self):
        n = len(self.times)
        if (n < 2 or self.times.ndim != 1 or not np.isfinite(self.times).all()
                or np.any(np.diff(self.times) <= 0)
                or not np.array_equal(self.times, self.tower_times)
                or not np.array_equal(self.times, [r["time_s"] for r in self.motion])):
            raise ValueError("Source time axes must be finite, increasing and identical")
        if not np.allclose(np.diff(self.times), 1 / self.manifest["source_fps"], atol=1e-9, rtol=0):
            raise ValueError("Source time axis is not the declared uniform sampling grid")
        if not np.allclose(self.times[[0, -1]], [self.manifest["segment"]["start_s"], self.manifest["segment"]["end_s"]], atol=1e-9, rtol=0):
            raise ValueError("Source segment differs from geometry times")
        expected = [(self.transforms, (n, 3, 19, 3, 4)), (self.poses, (n, 6)),
                    (self.tower_transforms, (n, len(self.heights), 3, 4)), (self.nacelles, (n, 3, 4)),
                    (self.rpm, (n,))]
        for values, shape in expected:
            if values.shape != shape or not np.isfinite(values).all():
                raise ValueError("Invalid source geometry/motion shape or nonfinite value")
        for values in (self.blade_reference, self.tower_reference, self.layout, self.heights):
            if not np.isfinite(values).all():
                raise ValueError("Nonfinite reference geometry")
        if np.any(np.diff(self.heights) <= 0):
            raise ValueError("Tower heights must increase")
        for tris, count in ((self.tower_triangles, len(self.tower_reference)), (self.blade_triangles, self.blade_reference.shape[1] * self.blade_reference.shape[2])):
            if tris.ndim != 2 or tris.shape[1] != 3 or not np.issubdtype(tris.dtype, np.integer) or np.any(tris < 0) or np.any(tris >= count):
                raise ValueError("Invalid reference surface topology")
        expected_pose = [[r['yaw_deg'], r['azimuth_deg'], r['rotor_speed_rpm'], *r['pitch_deg']] for r in self.motion]
        if not np.allclose(self.poses, expected_pose, atol=.001, rtol=0):
            raise ValueError("Motion and geometry poses differ")
        if not np.allclose(self.nacelles, [r['nacelle_transform'] for r in self.motion], atol=1e-8, rtol=0):
            raise ValueError("Motion and geometry nacelle transforms differ")
        self.pose_continuity = {}
        for name, values in (("blade", self.transforms), ("tower", self.tower_transforms), ("nacelle", self.nacelles)):
            rot = values[..., :3]
            if not np.allclose(np.swapaxes(rot, -1, -2) @ rot, np.eye(3), atol=2e-5, rtol=0) or np.any(np.linalg.det(rot) < .9999):
                raise ValueError("Source transforms must contain proper rotations")
            relative = np.swapaxes(rot[:-1], -1, -2) @ rot[1:]
            angle_step = np.degrees(np.arccos(np.clip((np.trace(relative, axis1=-2, axis2=-1) - 1) / 2, -1, 1)))
            maximum = float(angle_step.max())
            self.pose_continuity[name] = dict(max_rotation_step_deg=maximum, rejection_threshold_deg=90., passed=maximum < 90.)
            if maximum >= 90.:
                raise ValueError("Source pose discontinuity is too large for adjacent-sample interpolation")
        angle = np.asarray([r["azimuth_deg"] for r in self.motion])
        unwrapped = np.degrees(np.unwrap(np.radians(angle)))
        if not np.allclose(np.diff(angle), np.diff(unwrapped), atol=1e-6, rtol=0):
            raise ValueError("Source rotor angle must already be unwrapped")
        residual = np.diff(unwrapped) / np.diff(self.times) / 6 - (self.rpm[:-1] + self.rpm[1:]) / 2
        self.phase_check = dict(method="interval unwrapped phase derivative compared with endpoint-mean source RPM",
                                max_abs_residual_rpm=float(np.max(abs(residual))),
                                rms_residual_rpm=float(np.sqrt(np.mean(residual ** 2))),
                                tolerance_rpm=.05, passed=bool(np.max(abs(residual)) <= .05))
        if not self.phase_check["passed"]:
            raise ValueError("Source rotor phase and RPM consistency check failed")

    def locate(self, time_s):
        t = float(time_s)
        if not np.isfinite(t) or t < self.times[0] - 1e-9 or t > self.times[-1] + 1e-9:
            raise ValueError("Requested time is outside source geometry range")
        nearest = int(np.clip(np.searchsorted(self.times, t), 0, len(self.times) - 1))
        for i in (nearest, max(nearest - 1, 0)):
            if abs(t - self.times[i]) <= 1e-9:
                return i, i, 0., "source"
        right = nearest
        left = right - 1
        return left, right, float((t - self.times[left]) / (self.times[right] - self.times[left])), "interpolated"

    def sample(self, time_s, *, include_blades=False):
        """Return same-time world geometry, source mapping, RPM and signed labels.

        include_blades adds full solver section surfaces, useful for validation.
        No distance interpolation and no held event values are used.
        """
        left, right, alpha, kind = self.locate(time_s)
        pose = self.poses[left] * (1 - alpha) + self.poses[right] * alpha
        nacelle = (self.nacelles[left].copy() if left == right else
                   interpolate_transform(self.nacelles[left], self.nacelles[right], alpha))
        tower_transform = self.tower_transforms[left] * (1 - alpha) + self.tower_transforms[right] * alpha
        tr = tower_transform[self.tower_station]
        tower = np.einsum("nij,nj->ni", tr[..., :3], self.tower_reference) + tr[..., 3]
        tips = self.source_tips[left].copy()
        blades = []
        for b in range(3):
            if include_blades:
                def surface(index):
                    tr = self.transforms[index, b]
                    return np.einsum("sij,svj->svi", tr[..., :3], self.blade_reference[b]) + tr[:, None, :, 3]
                lo, hi = surface(left), surface(right)
            if left != right:
                h0, a0 = blade_root_frame(self.scalars, self.poses[left], b + 1, self.nacelles[left])
                h1, a1 = blade_root_frame(self.scalars, self.poses[right], b + 1, self.nacelles[right])
                hub, axes = blade_root_frame(self.scalars, pose, b + 1, nacelle)
                tips[b] = hub + (((self.source_tips[left, b] - h0) @ a0) * (1 - alpha)
                                 + ((self.source_tips[right, b] - h1) @ a1) * alpha) @ axes.T
                if include_blades:
                    lo = hub + (((lo - h0) @ a0) * (1 - alpha) + ((hi - h1) @ a1) * alpha) @ axes.T
            if include_blades:
                blades.append(lo.reshape(-1, 3) + self.layout)
        clearance, valid, reasons = [], [], []
        nearest = np.full((3, 3), np.nan)
        for b, tip in enumerate(tips):
            if tip[2] < tower[:, 2].min() or tip[2] > tower[:, 2].max():
                distance, reason = None, "no_tower_section_at_tip_height"
            else:
                try:
                    distance, wall = horizontal_clearance(tip, tower, self.tower_triangles)
                    nearest[b] = np.asarray(wall) + self.layout
                    reason = "valid"
                except ValueError as error:
                    if str(error) != "Tip height does not intersect moving tower":
                        raise
                    distance, reason = None, "no_tower_section_at_tip_height"
            clearance.append(distance)
            valid.append(distance is not None)
            reasons.append(reason)
        transform = np.eye(4)
        transform[:3] = nacelle
        transform[:3, 3] += self.layout
        result = dict(sim_time_s=float(time_s), source_left_index=left, source_right_index=right,
                      alpha=alpha, center_sample_kind=kind, pose=pose, rpm=float(self.rpm[left] * (1-alpha) + self.rpm[right] * alpha),
                      rpm_valid=True, rpm_reason="valid", tip_reference_world_m=tips + self.layout,
                      tower_points_world_m=tower + self.layout, nearest_tower_world_m=nearest,
                      clearance_m=clearance, clearance_valid=np.asarray(valid), clearance_reason=reasons,
                      nacelle_world_transform=transform)
        if include_blades:
            result["blade_points_world_m"] = np.asarray(blades)
        return result

    def audit_events(self, tolerance_m=.002):
        errors = []
        for row in self.measurements:
            if row.get("center_truth_m") is None:
                continue
            value = self.sample(row["time_s"])["clearance_m"][int(row["blade_id"]) - 1]
            if value is None:
                raise ValueError("Event center_truth has no reconstructable tower section")
            errors.append(abs(value - row["center_truth_m"]))
        maximum = max(errors) if errors else None
        return dict(reference="center_truth_m only; event truth_m is a different definition",
                    checked_events=len(errors), max_abs_error_m=maximum, tolerance_m=tolerance_m,
                    passed=bool(errors and maximum <= tolerance_m))


def frame_schedule(start_s, end_s, fps=20, exposure_s=None):
    """Integer PTS at 1/FPS; center-time labels; never append an endpoint frame."""
    if fps not in (15, 20, 25) or isinstance(fps, bool):
        raise ValueError("Supported constant frame rates are 15, 20 and 25")
    fps = int(fps)
    exposure_s = 1 / fps if exposure_s is None else float(exposure_s)
    start_s, end_s = float(start_s), float(end_s)
    if not all(np.isfinite(x) for x in (start_s, end_s, exposure_s)) or end_s <= start_s:
        raise ValueError("Segment must be finite and have positive duration")
    if not 0 < exposure_s <= 1 / fps:
        raise ValueError("Exposure must satisfy 0 < exposure_s <= 1/FPS")
    count = round((end_s - start_s) * fps)
    if count < 1 or abs(count / fps - (end_s - start_s)) > 1e-9:
        raise ValueError("Segment duration must contain an integer number of frames")
    rows = []
    for i in range(count):
        center = start_s + (i + .5) / fps
        rows.append(dict(frame_id=i, video_pts=i, video_time_s=i / fps, sim_time_s=center,
                         exposure_start_s=center - exposure_s / 2, exposure_end_s=center + exposure_s / 2))
    return rows


def build_frame_table(source, *, fps=20, exposure_s=None, start_s=None, end_s=None,
                      camera_mount=None, camera_config=None):
    start_s = source.times[0] if start_s is None else float(start_s)
    end_s = source.times[-1] if end_s is None else float(end_s)
    source.locate(start_s)
    source.locate(end_s)
    rows = frame_schedule(start_s, end_s, fps, exposure_s)
    mount = None if camera_mount is None else np.asarray(camera_mount, float)
    if mount is not None and (mount.shape != (4, 4) or not np.isfinite(mount).all()
                             or not np.allclose(mount[3], [0, 0, 0, 1])
                             or not np.allclose(mount[:3, :3].T @ mount[:3, :3], np.eye(3), atol=1e-6)
                             or np.linalg.det(mount[:3, :3]) < .99999):
        raise ValueError("camera_mount must be a rigid finite 4x4 transform")
    if camera_config is not None:
        if camera_config.get('turbine_id', source.turbine_id) != source.turbine_id:
            raise ValueError("Camera configuration turbine differs from source")
        configured_exposure = camera_config.get('exposure_s')
        actual_exposure = 1 / fps if exposure_s is None else float(exposure_s)
        if configured_exposure is not None and not np.isclose(configured_exposure, actual_exposure, atol=1e-12, rtol=0):
            raise ValueError("Camera configuration exposure differs from frame exposure")
        configured_mount = camera_config.get('camera_mount')
        if configured_mount is not None and (mount is None or not np.allclose(configured_mount, mount, atol=1e-9, rtol=0)):
            raise ValueError("Camera configuration mount differs from frame geometry")
    count = len(rows)
    geometry = dict(frame_id=np.arange(count), sim_time_s=np.asarray([r['sim_time_s'] for r in rows]),
                    tip_reference_world_m=np.empty((count, 3, 3)), nearest_tower_world_m=np.empty((count, 3, 3)),
                    clearance_valid=np.zeros((count, 3), bool), nacelle_world_transform=np.empty((count, 4, 4)),
                    camera_world_transform=np.full((count, 4, 4), np.nan), camera_transform_valid=np.full(count, mount is not None))
    for row in rows:
        i = row['frame_id']
        sample = source.sample(row['sim_time_s'])
        row['turbine_id'] = source.turbine_id
        for key in ('rpm', 'rpm_valid', 'rpm_reason', 'center_sample_kind', 'source_left_index', 'source_right_index', 'alpha'):
            row[key] = sample[key]
        for b in range(3):
            row[f'clearance_b{b+1}_m'] = sample['clearance_m'][b]
            row[f'clearance_b{b+1}_valid'] = bool(sample['clearance_valid'][b])
            row[f'clearance_b{b+1}_reason'] = sample['clearance_reason'][b]
        for key in ('tip_reference_world_m', 'nearest_tower_world_m', 'clearance_valid', 'nacelle_world_transform'):
            geometry[key][i] = sample[key]
        if mount is not None:
            geometry['camera_world_transform'][i] = sample['nacelle_world_transform'] @ mount
    contract = dict(fps=fps, frame_count=count, start_s=start_s, end_s=end_s,
                    duration_s=end_s-start_s, exposure_s=1/fps if exposure_s is None else float(exposure_s),
                    video_time_base=dict(numerator=1, denominator=fps), video_pts="frame_id",
                    sim_time="start_s + (frame_id + 0.5) / fps", endpoint_frame_included=False,
                    label_time="exposure center; image integrates exposure interval")
    identity = dict(source_sha256=source.source_hashes, turbine_id=source.turbine_id, time=contract,
                    camera_mount=None if mount is None else mount.tolist(), camera_config=camera_config,
                    clearance_definition=CLEARANCE_DEFINITION, interpolation=INTERPOLATION)
    dataset_id = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
    metadata = dict(schema="wfrl.camera-frame-data.v1", dataset_id=dataset_id, source_status=source.manifest['status'],
                    source_acceptance_status=source.manifest.get('acceptance_status'), source="FAST.Farm simulation postprocessing",
                    source_schema=source.manifest['schema'], source_sha256=source.source_hashes,
                    turbine_id=source.turbine_id, time_contract=contract, interpolation=INTERPOLATION,
                    clearance_definition_id=CLEARANCE_DEFINITION, tip_reference_definition=REFERENCE_DEFINITION,
                    reference_boundary="Solver terminal perimeter centroid; not structural output point, display tapered tip, or contour minimum",
                    coordinates="FAST.Farm world Cartesian XYZ, z up, metres; layout offset applied once",
                    missing_values="CSV empty; JSON null; NPZ NaN with explicit field validity masks",
                    camera_transform_reason="valid" if mount is not None else "camera_mount_not_supplied",
                    phase_check=source.phase_check, pose_continuity_check=source.pose_continuity,
                    source_geometry_fit_error_m=source.manifest.get('max_surface_fit_error_m'),
                    source_support_fit_error_m=source.manifest.get('max_support_fit_error_m'),
                    evidence_boundary="Synthetic simulation data; source REVIEW_ONLY and acceptance status are preserved; export consistency is not engineering measurement acceptance",
                    event_check=source.audit_events(),
                    center_sample_counts={kind:sum(r['center_sample_kind']==kind for r in rows) for kind in ('source','interpolated')},
                    clearance_valid_counts=geometry['clearance_valid'].sum(axis=0).tolist())
    if not metadata['event_check']['passed']:
        raise ValueError("Reconstructed reference-point clearance failed source center_truth audit")
    return rows, geometry, metadata


def export_frame_data(source, output_dir, **kwargs):
    """Stage sidecars and publish them without replacing existing files.

    ``data_manifest.json`` is the commit marker. If interrupted between file
    publication and the marker, a retry with identical inputs verifies the
    stage and any published hashes before completing the same export.
    """
    output = Path(output_dir).resolve()
    if output == source.path or source.path in output.parents:
        raise ValueError("Camera export must not write inside the source package")
    names = ('frames.csv', 'frame_geometry.npz', 'data_manifest.json')
    stage = output / '.frame-data-stage'
    if (output / names[-1]).exists():
        raise FileExistsError("Frame data already committed; use a new output directory")
    if any((output / name).exists() for name in names) and not stage.exists():
        raise FileExistsError("Unstaged partial frame data exists; preserve it and use a new output directory")
    rows, geometry, metadata = build_frame_table(source, **kwargs)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'checks').mkdir(exist_ok=True)
    intent = {'schema': 'wfrl.camera-frame-stage.v1', 'metadata': metadata}
    if stage.exists():
        intent_path = stage / 'intent.json'
        if not intent_path.is_file() or json.loads(intent_path.read_text()) != intent:
            raise ValueError("Frame-data stage inputs differ or its intent is missing; use a new output directory")
    else:
        stage.mkdir()
        (stage / 'intent.json').write_text(json.dumps(intent, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    complete = stage / 'data_manifest.json'
    if not complete.exists():
        if any((output / name).exists() for name in names):
            raise ValueError("Incomplete stage cannot verify existing published frame data; use a new output directory")
        # The matching intent identifies these stage files as this export's own
        # unpublished work. Rebuilding them after interruption is safe.
        with (stage / 'frames.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        np.savez_compressed(stage / 'frame_geometry.npz', **geometry)
        metadata['files'] = {name: sha256(stage / name) for name in names[:2]}
        temporary = stage / 'data_manifest.json.tmp'
        temporary.write_text(json.dumps(metadata, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(complete)
    else:
        staged = json.loads(complete.read_text())
        if {k: v for k, v in staged.items() if k != 'files'} != metadata:
            raise ValueError("Completed frame-data stage metadata differs from requested export")
        metadata = staged
    expected = dict(metadata['files'], **{'data_manifest.json': sha256(complete)})
    for name in names:
        if not (stage / name).is_file() or sha256(stage / name) != expected[name]:
            raise ValueError("Frame-data stage integrity mismatch: " + name)
        target = output / name
        if target.exists() and sha256(target) != expected[name]:
            raise FileExistsError("Published partial frame data differs from verified stage: " + name)
    for name in names:
        target = output / name
        try:
            # Stage and output share a filesystem. Hard links publish complete
            # files atomically and O_EXCL semantics prevent replacement races.
            os.link(stage / name, target)
        except FileExistsError:
            if sha256(target) != expected[name]:
                raise FileExistsError("Frame-data destination changed during publication: " + name)
    for name in (*names, 'intent.json'):
        (stage / name).unlink()
    # Preserve unexpected files rather than deleting an unfamiliar directory.
    if not any(stage.iterdir()):
        stage.rmdir()
    return metadata
