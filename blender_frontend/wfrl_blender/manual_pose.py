"""Validated pose values and portable presets for rigid manual turbines."""

from collections.abc import Mapping
import json
import math
from numbers import Real


POSE_FIELDS = (
    'yaw_deg', 'pitch_deg', 'rpm', 'azimuth_deg', 'individual_pitch',
    'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg',
)
POSE_SCHEMA = 'wfrl.manual-pose.v1'
_TURBINES = ('T1', 'T2', 'T3')
_ANGLE_RANGES = {
    'yaw_deg': (-180.0, 180.0),
    'pitch_deg': (-30.0, 90.0),
    'pitch_b1_deg': (-30.0, 90.0),
    'pitch_b2_deg': (-30.0, 90.0),
    'pitch_b3_deg': (-30.0, 90.0),
}
_VALUE_RANGES = {
    **_ANGLE_RANGES,
    'rpm': (0.0, 30.0),
    'azimuth_deg': (0.0, 360.0),
}


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{name} must be a number')
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f'{name} must be finite') from exc
    if not math.isfinite(number):
        raise ValueError(f'{name} must be finite')
    return number


def _fields(values, expected, name):
    if not isinstance(values, Mapping):
        raise ValueError(f'{name} must be an object')
    if set(values) != set(expected):
        raise ValueError(f'{name} must contain exactly: {", ".join(expected)}')


def pose_values(values):
    """Return a complete normalized pose without changing out-of-range values."""
    _fields(values, POSE_FIELDS, 'Pose')
    normalized = {}
    for field in POSE_FIELDS:
        value = values[field]
        if field == 'individual_pitch':
            if type(value) is not bool:
                raise ValueError('individual_pitch must be a boolean')
            normalized[field] = value
            continue
        number = _number(value, field)
        lower, upper = _VALUE_RANGES[field]
        if not lower <= number <= upper:
            raise ValueError(f'{field} must be between {lower:g} and {upper:g}')
        normalized[field] = number
    return normalized


def _poses(poses):
    if not isinstance(poses, Mapping):
        raise ValueError('Turbines must be an object')
    if not poses or any(key not in _TURBINES for key in poses):
        raise ValueError('Turbines must contain at least one of T1, T2, T3 only')
    return {key: pose_values(poses[key]) for key in _TURBINES if key in poses}


def encode_poses(poses):
    """Encode one or more complete turbine poses using the manual pose schema."""
    payload = {'schema': POSE_SCHEMA, 'turbines': _poses(poses)}
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON field: {key}')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f'Non-finite JSON number: {value}')


def decode_poses(text):
    """Decode complete turbine poses, rejecting unknown fields and duplicate keys."""
    if not isinstance(text, str):
        raise ValueError('Pose JSON must be text')
    payload = json.loads(
        text, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
    )
    _fields(payload, ('schema', 'turbines'), 'Pose document')
    if payload['schema'] != POSE_SCHEMA:
        raise ValueError(f'Pose schema must be {POSE_SCHEMA}')
    return _poses(payload['turbines'])


def preset_changes(kind):
    """Return only the fields controlled by the requested orientation preset."""
    if kind == 'BLADE_DOWN':
        return {'azimuth_deg': 180.0}
    if kind == 'BLADE_SIDE':
        return {'azimuth_deg': 90.0}
    if kind == 'FEATHER':
        return {
            'pitch_deg': 90.0, 'individual_pitch': False,
            'pitch_b1_deg': 90.0, 'pitch_b2_deg': 90.0, 'pitch_b3_deg': 90.0,
        }
    raise ValueError(f'Unknown pose preset: {kind}')


def adjusted_angle(field, value, delta):
    """Clamp yaw and pitch adjustments and wrap rotor phase into [0, 360)."""
    if field not in _ANGLE_RANGES and field != 'azimuth_deg':
        raise ValueError(f'Unsupported angle field: {field}')
    value = _number(value, field)
    delta = _number(delta, 'Angle adjustment')
    if field == 'azimuth_deg':
        return (value % 360.0 + delta % 360.0) % 360.0
    lower, upper = _ANGLE_RANGES[field]
    return max(lower, min(upper, value + delta))
