"""Sensor geometry and provenance helpers used by the Blender presentation."""
from __future__ import annotations

import math
from typing import Iterable


class SensorPayloadError(ValueError):
    pass


def _vec(values: Iterable[float], label: str) -> tuple[float, float, float]:
    try:
        result = tuple(float(item) for item in values)
    except (TypeError, ValueError) as exc:
        raise SensorPayloadError(f"{label} must be a 3-vector") from exc
    if len(result) != 3 or not all(math.isfinite(item) for item in result):
        raise SensorPayloadError(f"{label} must be a finite 3-vector")
    return result


def normalize(vector: Iterable[float], label: str = "vector") -> tuple[float, float, float]:
    result = _vec(vector, label)
    length = math.sqrt(sum(item * item for item in result))
    if length <= 1e-12:
        raise SensorPayloadError(f"{label} must not be zero")
    return tuple(item / length for item in result)


def frustum_vertices(origin: Iterable[float], forward: Iterable[float], up: Iterable[float],
                     *, fov_deg: float, near_m: float, far_m: float) -> tuple[tuple[float, float, float], ...]:
    """Return 8 corners ordered near-plane then far-plane."""
    o = _vec(origin, "origin")
    f = normalize(forward, "forward")
    u = normalize(up, "up")
    right = normalize((f[1] * u[2] - f[2] * u[1], f[2] * u[0] - f[0] * u[2], f[0] * u[1] - f[1] * u[0]), "right")
    try:
        fov = float(fov_deg)
        near = float(near_m)
        far = float(far_m)
    except (TypeError, ValueError) as exc:
        raise SensorPayloadError("fov_deg, near_m, and far_m must be numeric") from exc
    if not math.isfinite(fov) or not 0 < fov < 179:
        raise SensorPayloadError("fov_deg must be between 0 and 179")
    if not math.isfinite(near) or not math.isfinite(far) or not 0 < near < far:
        raise SensorPayloadError("near_m and far_m must be positive and ordered")
    tan_half = math.tan(math.radians(fov) / 2.0)
    corners = []
    for distance in (near, far):
        center = tuple(o[i] + f[i] * distance for i in range(3))
        half = distance * tan_half
        corners.extend(tuple(center[i] + sx * right[i] * half + sy * u[i] * half for i in range(3))
                       for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)))
    return tuple(corners)
