"""Validated, offline reconstruction playback data for the original forward model.

This module only reads saved reconstruction states and evaluates Rotor.forward.
It neither changes the solver nor runs a reconstruction. Observation masks remain
distinct from optional synthetic truth, including when source frames are skipped.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np

from ._vendor.blade_recon import model


EXTERNAL_SOURCE_LABEL = 'External reconstruction; provenance not supplied'
MODEL_SHA256 = hashlib.sha256(Path(model.__file__).read_bytes()).hexdigest()


def default_recon_path() -> Path:
    return Path(__file__).resolve().parent / 'assets' / 'blade_recon_synth20' / 'recon.json'


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot read reconstruction JSON {path}: {exc}') from exc


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite number')
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f'{label} must be a finite number') from exc
    if not np.isfinite(result):
        raise ValueError(f'{label} must be a finite number')
    return result


def _integer(value, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f'{label} must be an integer >= {minimum}')
    # Source frames are exposed as int64 arrays, with no silent wraparound.
    if value > np.iinfo(np.int64).max:
        raise ValueError(f'{label} exceeds the supported integer range')
    return value


def _array(value, shape, label: str, nonnegative: bool = False) -> np.ndarray:
    try:
        raw = np.asarray(value, dtype=object)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{label} must have shape {shape}') from exc
    if raw.shape != shape:
        raise ValueError(f'{label} must have shape {shape}, got {raw.shape}')
    arr = np.array([_number(v, label) for v in raw.flat], dtype=float).reshape(shape)
    if nonnegative and np.any(arr < 0):
        raise ValueError(f'{label} must be nonnegative')
    return arr


def _config(data) -> model.TurbineConfig:
    if not isinstance(data, dict):
        raise ValueError('turbine must be an object')
    values = model.TurbineConfig().to_dict()
    for key in values:
        if key not in data:
            continue
        if key in ('n_sections', 'n_ring'):
            values[key] = _integer(data[key], f'turbine.{key}', 2 if key == 'n_sections' else 4)
        elif key == 'spin':
            if isinstance(data[key], bool) or type(data[key]) is not int or data[key] not in (-1, 1):
                raise ValueError('turbine.spin must be +1 or -1')
            values[key] = data[key]
        else:
            values[key] = _number(data[key], f'turbine.{key}')
    if values['n_ring'] % 2:
        raise ValueError('turbine.n_ring must be even')
    if values['n_sections'] > 4096 or values['n_ring'] > 1024 or values['n_sections'] * values['n_ring'] > 1_048_576:
        raise ValueError('turbine mesh sampling is too large')
    if not 0 < values['hub_radius_m'] < values['tip_radius_m']:
        raise ValueError('turbine radii must satisfy 0 < hub_radius_m < tip_radius_m')
    if values['hub_height_m'] <= 0:
        raise ValueError('turbine.hub_height_m must be positive')
    for key in ('tilt_deg', 'cone_deg'):
        if abs(values[key]) >= 90:
            raise ValueError(f'turbine.{key} must be between -90 and +90 degrees')
    return model.TurbineConfig(**values)


def _records(data, label: str, *, require_time: bool):
    if not isinstance(data, dict):
        raise ValueError(f'{label} must be an object')
    frames = data.get('frames')
    if not isinstance(frames, list) or not frames:
        raise ValueError(f'{label}.frames must be a nonempty list')
    states, ids, times = [], [], []
    last_id = -1
    last_time = None
    for i, frame in enumerate(frames):
        prefix = f'{label}.frames[{i}]'
        if not isinstance(frame, dict):
            raise ValueError(f'{prefix} must be an object')
        frame_id = _integer(frame.get('frame'), f'{prefix}.frame')
        if frame_id <= last_id:
            raise ValueError(f'{label} frame ids must be strictly increasing')
        last_id = frame_id
        time = _number(frame['t'], f'{prefix}.t') if 't' in frame else None
        if require_time and time is None:
            raise ValueError(f'{prefix}.t is required')
        if time is not None:
            if time < 0:
                raise ValueError(f'{prefix}.t must be nonnegative')
            if last_time is not None and time <= last_time:
                raise ValueError(f'{label} times must be strictly increasing')
            last_time = time
        states.append(_array(frame.get('state'), (model.N_STATE,), f'{prefix}.state'))
        if 'std' in frame:
            _array(frame['std'], (model.N_STATE,), f'{prefix}.std', nonnegative=True)
        ids.append(frame_id)
        times.append(time)
    return frames, np.stack(states), np.array(ids, dtype=np.int64), times


def _observations(frames, sections: int) -> np.ndarray:
    masks = []
    for i, frame in enumerate(frames):
        label = f'reconstruction.frames[{i}].observed_sections'
        try:
            raw = np.asarray(frame.get('observed_sections'), dtype=object)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{label} must have shape (3, {sections})') from exc
        if raw.shape != (3, sections):
            raise ValueError(f'{label} must have shape (3, {sections})')
        # Boss output uses exact JSON integers 0/1. Do not accept arbitrary truthy
        # values or strings, which would mislabel inferred sections as observed.
        if any(type(v) is not bool and (type(v) is not int or v not in (0, 1)) for v in raw.flat):
            raise ValueError(f'{label} must contain booleans or exact integers 0/1')
        masks.append(np.asarray(raw, dtype=bool))
    return np.stack(masks)


def _cameras(data) -> list[dict]:
    if data is None:
        return []
    if not isinstance(data, dict) or not isinstance(data.get('cameras'), list) or not data['cameras']:
        raise ValueError('cameras.cameras must be a nonempty list')
    result = []
    for i, camera in enumerate(data['cameras']):
        prefix = f'cameras.cameras[{i}]'
        if not isinstance(camera, dict):
            raise ValueError(f'{prefix} must be an object')
        W = _integer(camera.get('W'), f'{prefix}.W', 1)
        H = _integer(camera.get('H'), f'{prefix}.H', 1)
        K = _array(camera.get('K'), (3, 3), f'{prefix}.K')
        if np.linalg.matrix_rank(K) != 3 or K[0, 0] <= 0 or K[1, 1] <= 0:
            raise ValueError(f'{prefix}.K must be invertible with positive focal lengths')
        transform = camera.get('T_cv_from_world')
        try:
            transform_shape = np.asarray(transform, dtype=object).shape
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{prefix}.T_cv_from_world must have shape (3, 4) or (4, 4)') from exc
        if transform_shape not in ((3, 4), (4, 4)):
            raise ValueError(f'{prefix}.T_cv_from_world must have shape (3, 4) or (4, 4)')
        T = _array(transform, transform_shape, f'{prefix}.T_cv_from_world')
        if transform_shape == (4, 4) and not np.allclose(T[3], [0, 0, 0, 1], atol=1e-12, rtol=0):
            raise ValueError(f'{prefix}.T_cv_from_world has an invalid homogeneous row')
        if np.linalg.matrix_rank(T[:3, :3]) != 3:
            raise ValueError(f'{prefix}.T_cv_from_world must be invertible')
        normal = copy.deepcopy(camera)
        normal.update(W=W, H=H, K=K.tolist(), T_cv_from_world=T[:3].tolist())
        result.append(normal)
    return result


class ReconSequence:
    """Saved states with verified geometry, observation coverage and provenance.

    Times and source_frames retain the original source values. Optional truth is
    aligned by its frame field. Missing truth rows contain NaN and are reported as
    None by truth_geometry; those rows never stand in for reconstruction states.
    """

    model_sha256 = MODEL_SHA256

    def __init__(self, path, truth_path=None, cameras_path=None):
        path = Path(path).expanduser().resolve()
        data = _read_json(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        truth = Path(truth_path).expanduser().resolve() if truth_path is not None else path.with_name('truth.json')
        cameras = Path(cameras_path).expanduser().resolve() if cameras_path is not None else path.with_name('cameras.json')
        truth_data = _read_json(truth) if truth_path is not None or truth.is_file() else None
        cameras_data = _read_json(cameras) if cameras_path is not None or cameras.is_file() else None
        manifest_path = path.with_name('manifest.json')
        source_label = EXTERNAL_SOURCE_LABEL
        manifest = None
        if manifest_path.is_file():
            try:
                candidate = _read_json(manifest_path)
                record = candidate.get('files', {}).get(path.name, {})
                if isinstance(record, dict) and record.get('sha256') == digest:
                    manifest = candidate
            except (ValueError, AttributeError):
                # An unrelated or malformed sidecar supplies no provenance.
                pass
        if manifest is not None:
            source_label = manifest.get('source_label', EXTERNAL_SOURCE_LABEL)
            if not isinstance(source_label, str) or not source_label.strip():
                source_label = EXTERNAL_SOURCE_LABEL
            claimed_model = manifest.get('model', {}).get('sha256')
            if claimed_model and claimed_model != MODEL_SHA256:
                raise ValueError('Provenance model hash does not match the bundled forward model')
            for companion, companion_data in ((truth, truth_data), (cameras, cameras_data)):
                record = manifest.get('files', {}).get(companion.name, {})
                if companion_data is not None and isinstance(record, dict) and record.get('sha256'):
                    if record['sha256'] != hashlib.sha256(companion.read_bytes()).hexdigest():
                        raise ValueError(f'Provenance hash mismatch for {companion.name}')
        self._initialize(data, truth_data, cameras_data, source_label, digest)
        self.path = path
        self.manifest = manifest
        self.truth_path = truth if truth_data is not None else None
        self.cameras_path = cameras if cameras_data is not None else None

    @classmethod
    def from_data(cls, data, truth_data=None, cameras_data=None,
                  source_label=EXTERNAL_SOURCE_LABEL, source_sha256=''):
        """Validate JSON objects embedded in a .blend without temporary files."""
        instance = cls.__new__(cls)
        instance._initialize(data, truth_data, cameras_data, source_label, source_sha256)
        instance.path = None
        instance.manifest = None
        instance.truth_path = None
        instance.cameras_path = None
        return instance

    def _initialize(self, data, truth_data, cameras_data, source_label, source_sha256):
        data = copy.deepcopy(data)
        if not isinstance(data, dict):
            raise ValueError('reconstruction must be an object')
        cfg = _config(data.get('turbine'))
        fps = _number(data.get('fps'), 'reconstruction.fps')
        if fps <= 0:
            raise ValueError('reconstruction.fps must be positive')
        if 'state_names' in data and data['state_names'] != model.STATE_NAMES:
            raise ValueError('reconstruction.state_names does not match the original model')
        frames, states, source_frames, times = _records(data, 'reconstruction', require_time=True)
        observed = _observations(frames, cfg.n_sections)
        truth_states = None
        if truth_data is not None:
            _, truth, truth_ids, truth_times = _records(truth_data, 'truth', require_time=False)
            by_frame = {int(frame_id): (state, time) for frame_id, state, time in zip(truth_ids, truth, truth_times)}
            truth_states = np.full(states.shape, np.nan)
            for i, frame_id in enumerate(source_frames):
                match = by_frame.get(int(frame_id))
                if match is not None:
                    state, time = match
                    if time is not None and not np.isclose(times[i], time, atol=1e-9, rtol=0):
                        raise ValueError(f'truth time does not match reconstruction at source frame {frame_id}')
                    truth_states[i] = state
        cameras = _cameras(cameras_data)
        for label, companion in (('truth', truth_data), ('cameras', cameras_data)):
            if companion is not None and 'turbine' in companion:
                if _config(companion['turbine']).to_dict() != cfg.to_dict():
                    raise ValueError(f'{label}.turbine does not match reconstruction')
            if companion is not None and 'fps' in companion:
                if _number(companion['fps'], f'{label}.fps') != fps:
                    raise ValueError(f'{label}.fps does not match reconstruction')
        self.rotor = model.Rotor(cfg)
        self.frames = frames
        self.states = states
        self.observed = observed
        self.times = np.asarray(times, dtype=float)
        self.source_frames = source_frames
        self.fps = fps
        self.truth_states = truth_states
        self.cameras = cameras
        self.source_label = source_label if isinstance(source_label, str) and source_label.strip() else EXTERNAL_SOURCE_LABEL
        self.source_sha256 = source_sha256
        self.data = data
        for arr in (self.states, self.observed, self.times, self.source_frames, self.truth_states):
            if arr is not None:
                arr.setflags(write=False)

    def _index(self, index):
        if isinstance(index, bool) or not isinstance(index, (int, np.integer)) or not 0 <= index < len(self.frames):
            raise IndexError('reconstruction frame index is out of range')
        return int(index)

    def _geometry(self, state):
        with np.errstate(over='ignore', invalid='ignore'):
            V, A = self.rotor.forward(state)
        if not np.isfinite(V).all() or not np.isfinite(A).all():
            raise ValueError('Saved state produces nonfinite forward-model geometry')
        return V, A

    def geometry(self, index):
        return self._geometry(self.states[self._index(index)])

    def truth_geometry(self, index):
        index = self._index(index)
        if self.truth_states is None or not np.isfinite(self.truth_states[index]).all():
            return None
        return self._geometry(self.truth_states[index])
