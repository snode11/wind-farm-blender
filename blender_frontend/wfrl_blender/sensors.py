"""Sensor geometry and provenance helpers used by the Blender presentation."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping


class SensorPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class LidarRay:
    origin: tuple[float, float, float]
    direction: tuple[float, float, float]
    range_m: float
    endpoint: tuple[float, float, float]
    fidelity: str = "DERIVED"
    provenance: str = "Nacelle lidar beam geometry"


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


def lidar_rays(origin: Iterable[float], forward: Iterable[float], ranges: Iterable[float],
               *, half_angle_deg: float = 15.0) -> tuple[LidarRay, ...]:
    """Make a center beam plus a deterministic four-beam cone."""
    o = _vec(origin, "origin")
    f = normalize(forward, "forward")
    try:
        ranges_tuple = tuple(float(value) for value in ranges)
    except (TypeError, ValueError) as exc:
        raise SensorPayloadError("ranges must be positive finite values") from exc
    if not ranges_tuple or any(not math.isfinite(value) or value <= 0 for value in ranges_tuple):
        raise SensorPayloadError("ranges must be positive finite values")
    try:
        half_angle = math.radians(float(half_angle_deg))
    except (TypeError, ValueError) as exc:
        raise SensorPayloadError("half_angle_deg must be between 0 and 90") from exc
    if not 0 < half_angle < math.pi / 2:
        raise SensorPayloadError("half_angle_deg must be between 0 and 90")
    # Choose a stable perpendicular basis even for a near-vertical beam.
    helper = (0.0, 0.0, 1.0) if abs(f[2]) < 0.9 else (0.0, 1.0, 0.0)
    side = normalize((f[1] * helper[2] - f[2] * helper[1],
                      f[2] * helper[0] - f[0] * helper[2],
                      f[0] * helper[1] - f[1] * helper[0]), "beam side")
    up = (side[1] * f[2] - side[2] * f[1],
          side[2] * f[0] - side[0] * f[2],
          side[0] * f[1] - side[1] * f[0])
    directions = [f]
    for index in range(4):
        theta = math.tau * index / 4.0
        lateral = tuple(math.cos(theta) * side[i] + math.sin(theta) * up[i] for i in range(3))
        direction = normalize(tuple(math.cos(half_angle) * f[i] + math.sin(half_angle) * lateral[i] for i in range(3)), "beam direction")
        directions.append(direction)
    return tuple(
        LidarRay(o, direction, distance,
                 tuple(o[i] + direction[i] * distance for i in range(3)))
        for distance in ranges_tuple for direction in directions
    )


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


def validate_sensor_payload(payload: Mapping[str, object]) -> dict:
    if not isinstance(payload, Mapping):
        raise SensorPayloadError("sensor payload must be an object")
    fidelity = str(payload.get("fidelity", "SYNTH")).upper()
    if fidelity not in {"SYNTH", "DIRECT", "DERIVED", "EXPORTED"}:
        raise SensorPayloadError("invalid sensor fidelity")
    provenance = payload.get("provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        raise SensorPayloadError("sensor provenance is required")
    return {"sensor": str(payload.get("sensor", "unknown")),
            "fidelity": fidelity, "provenance": provenance,
            "validity": str(payload.get("validity", "valid"))}
