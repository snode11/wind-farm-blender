"""Bounded wake payloads and Blender-friendly wake geometry.

The module deliberately keeps the data contract independent from ``bpy``.  A
FLORIS proxy is marked ``SYNTH`` while a FAST.Farm DisXY export is marked
``EXPORTED``; neither path is allowed to silently become telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping


MAX_WAKE_POINTS = 250_000
VALID_FIDELITY = frozenset(("SYNTH", "DIRECT", "EXPORTED"))
VALID_KINDS = frozenset(("floris_proxy", "disxy"))


class WakePayloadError(ValueError):
    """The payload is malformed, oversized, or has an unsafe fidelity label."""


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise WakePayloadError(f"{label} must be finite")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise WakePayloadError(f"{label} must be finite") from exc
    if not math.isfinite(value):
        raise WakePayloadError(f"{label} must be finite")
    return value


def _number_list(values: Any, label: str, maximum: int = MAX_WAKE_POINTS) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)):
        raise WakePayloadError(f"{label} must be an array")
    if len(values) > maximum:
        raise WakePayloadError(f"{label} exceeds {maximum} values")
    return tuple(_finite(value, label) for value in values)


@dataclass(frozen=True)
class WakeFrame:
    """Validated, immutable wake data used by the main Blender thread."""

    kind: str
    sequence: int
    fidelity: str
    provenance: str
    validity: str
    turbine_ids: tuple[str, ...]
    timestamp_s: float
    values: tuple[float, ...]
    shape: tuple[int, ...]
    x: tuple[float, ...] = ()
    y: tuple[float, ...] = ()
    z: float | None = None
    vectors: tuple[float, ...] = ()

    @property
    def point_count(self) -> int:
        return len(self.values)

    @property
    def source_label(self) -> str:
        return "FLORIS proxy" if self.kind == "floris_proxy" else "FAST.Farm DisXY"

    @property
    def is_renderable(self) -> bool:
        return self.validity == "valid" and bool(self.values) and bool(self.shape)


def _channel_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise WakePayloadError("wake data must be an object")
    if data.get("validity") == "valid" and not isinstance(data.get("value"), Mapping):
        raise WakePayloadError("valid wake data.value must be an object")
    return data


def validate_wake_payload(payload: Mapping[str, Any], *, sequence: int = 0) -> WakeFrame:
    """Validate the JSON wake payload frozen by the v1 wire protocol.

    Accepted ``data.value`` fields are ``grid``/``values`` for DisXY and
    ``points``/``values`` for a proxy.  The compact aliases make it possible to
    feed old recorded samples without changing the on-wire fidelity metadata.
    """
    if not isinstance(payload, Mapping):
        raise WakePayloadError("wake payload must be an object")
    if payload.get("encoding") != "json":
        raise WakePayloadError("only JSON wake payloads are supported")
    ids = payload.get("turbine_ids")
    if not isinstance(ids, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in ids):
        raise WakePayloadError("wake turbine_ids must be non-empty text")
    if len(set(ids)) != len(ids):
        raise WakePayloadError("wake turbine_ids must be unique")
    data = _channel_payload(payload)
    validity = str(data.get("validity", "valid"))
    if validity != "valid":
        return WakeFrame(
            kind=str(payload.get("kind", "floris_proxy")), sequence=int(sequence),
            fidelity=str(data.get("fidelity", "SYNTH")),
            provenance=str((data.get("provenance") or {}).get("channel", "wake")),
            validity=validity, turbine_ids=tuple(ids), timestamp_s=0.0,
            values=(), shape=(),
        )
    value = data.get("value") if isinstance(data.get("value"), Mapping) else data
    kind = str(value.get("kind", data.get("kind", payload.get("kind", "floris_proxy")))).lower()
    if kind in ("floris", "proxy"):
        kind = "floris_proxy"
    if kind not in VALID_KINDS:
        raise WakePayloadError(f"unsupported wake kind: {kind}")
    default_fidelity = "SYNTH" if kind == "floris_proxy" else "EXPORTED"
    fidelity = str(value.get("fidelity", data.get("fidelity", default_fidelity))).upper()
    if fidelity not in VALID_FIDELITY:
        raise WakePayloadError("wake fidelity must be SYNTH, DIRECT, or EXPORTED")
    provenance = value.get("provenance") or data.get("provenance") or {}
    if isinstance(provenance, Mapping):
        provenance_text = str(provenance.get("formula") or provenance.get("file") or provenance.get("channel") or "")
    else:
        provenance_text = str(provenance)
    if not provenance_text.strip():
        raise WakePayloadError("wake provenance is required")
    values = value.get("values", value.get("u", ()))
    values_tuple = _number_list(values, "wake values")
    shape_value = value.get("shape")
    if shape_value is None:
        grid = value.get("grid") or {}
        if isinstance(grid, Mapping) and grid.get("shape") is not None:
            shape_value = grid.get("shape")
    if not isinstance(shape_value, (list, tuple)) or not shape_value:
        raise WakePayloadError("wake shape is required")
    try:
        shape = tuple(int(item) for item in shape_value)
    except (TypeError, ValueError) as exc:
        raise WakePayloadError("wake shape must contain positive integers") from exc
    if any(item <= 0 for item in shape) or math.prod(shape) != len(values_tuple):
        raise WakePayloadError("wake shape does not match values")
    if len(values_tuple) > MAX_WAKE_POINTS:
        raise WakePayloadError("wake payload is too large")
    x = _number_list(value.get("x", ()), "wake x")
    y = _number_list(value.get("y", ()), "wake y")
    z_value = value.get("z")
    z = None if z_value is None else _finite(z_value, "wake z")
    vectors = _number_list(value.get("vectors", value.get("vec", ())), "wake vectors")
    if vectors and len(vectors) != len(values_tuple) * 3:
        raise WakePayloadError("wake vectors must contain three values per sample")
    timestamp_value = payload.get("timestamp", data.get("timestamp", 0.0))
    if isinstance(timestamp_value, Mapping):
        timestamp_value = timestamp_value.get("value", 0.0)
    timestamp = _finite(timestamp_value, "wake timestamp")
    return WakeFrame(kind, int(sequence), fidelity, provenance_text, validity,
                     tuple(ids), timestamp, values_tuple, shape, x, y, z, vectors)


def proxy_ring_points(hub: Iterable[float], rotor_radius: float, wind_direction_deg: float,
                      phase: float, *, rings: int = 26, segments: int = 40,
                      length_diameters: float = 2.5, expansion: float = 0.7) -> tuple[tuple[float, float, float], ...]:
    """Return a deterministic expanding ring tube for a SYNTH proxy."""
    if rings <= 0 or segments < 3:
        raise ValueError("rings must be positive and segments must be at least 3")
    hx, hy, hz = (_finite(v, "hub coordinate") for v in hub)
    radius = _finite(rotor_radius, "rotor_radius")
    if radius <= 0:
        raise ValueError("rotor_radius must be positive")
    angle = math.radians(float(wind_direction_deg) - 270.0)
    dx, dy = math.cos(angle), math.sin(angle)
    ex, ey = dy, -dx
    length = length_diameters * 2.0 * radius
    result = []
    for ring in range(rings):
        frac = (ring / rings + float(phase)) % 1.0
        ring_radius = radius * (1.0 + expansion * frac)
        for segment in range(segments):
            theta = math.tau * segment / segments
            result.append((hx + frac * length * dx + ring_radius * math.sin(theta) * ex,
                           hy + frac * length * dy + ring_radius * math.sin(theta) * ey,
                           hz + ring_radius * math.cos(theta)))
    return tuple(result)


def grid_mesh(frame: WakeFrame) -> tuple[tuple[tuple[float, float, float], ...], tuple[tuple[int, ...], ...]]:
    """Convert a DisXY frame to vertices and quad faces without numpy/Blender."""
    if frame.kind != "disxy" or len(frame.shape) != 2:
        raise WakePayloadError("grid_mesh requires a 2-D DisXY frame")
    ny, nx = frame.shape
    if len(frame.x) != nx or len(frame.y) != ny:
        raise WakePayloadError("DisXY x/y axes do not match shape")
    z = frame.z or 0.0
    vertices = tuple((frame.x[col], frame.y[row], z) for row in range(ny) for col in range(nx))
    faces = []
    for row in range(ny - 1):
        for col in range(nx - 1):
            i = row * nx + col
            faces.append((i, i + 1, i + nx + 1, i + nx))
    return vertices, tuple(faces)


class WakeFrameBuffer:
    """Keep only the newest replaceable frame and expose queue diagnostics."""

    def __init__(self):
        self._frame: WakeFrame | None = None
        self.dropped = 0

    def push(self, frame: WakeFrame) -> bool:
        if self._frame is not None and frame.sequence <= self._frame.sequence:
            self.dropped += 1
            return False
        if self._frame is not None:
            self.dropped += 1
        self._frame = frame
        return True

    def pop_latest(self) -> WakeFrame | None:
        frame, self._frame = self._frame, None
        return frame

    @property
    def pending(self) -> int:
        return int(self._frame is not None)


PROXY_LINE_COUNT = 16
PROXY_RING_COUNT = 22
PROXY_PULSE_COUNT = 12


def wake_section(u: float, phase: float, rotor_radius: float = 63.0):
    """Shared bounded centerline and expansion, in hub-local meters."""
    travel = phase / PROXY_RING_COUNT
    cy = 4.0 * u * u * math.sin(math.tau * (1.25 * u - travel))
    cz = 2.0 * u * u * math.sin(math.tau * (.8 * u - travel))
    radius = rotor_radius * (.88 + .24 * u) * (1 + .012 * u * math.sin(math.tau * (u - travel)))
    return cy, cz, radius


def flow_point(u: float, angle: float, phase: float, rotor_radius: float = 63.0):
    cy, cz, radius = wake_section(u, phase, rotor_radius)
    return (12 + 445 * u, cy + radius * math.cos(angle), cz + radius * math.sin(angle), 1.0)


def proxy_line_points(index: int, count: int, phase: float, rotor_radius: float = 63.0):
    """Longitudinal traces follow the same centerline as the moving rings."""
    angle = math.tau * index / PROXY_LINE_COUNT
    for j in range(count):
        u = j / max(1, count - 1)
        yield flow_point(u, angle, phase, rotor_radius)


def proxy_ring_points_local(index: int, count: int, phase: float, rotor_radius: float = 63.0):
    """Phase is unbounded: ring identities cross spacing boundaries continuously."""
    u = ((index + phase) / PROXY_RING_COUNT) % 1.0
    for j in range(count):
        yield flow_point(u, math.tau * j / count, phase, rotor_radius)


def proxy_pulse_points(index: int, count: int, phase: float, rotor_radius: float = 63.0):
    """Short tapered bright tracers advect along existing longitudinal traces."""
    head = (index / PROXY_PULSE_COUNT + phase / PROXY_RING_COUNT) % 1.0
    angle = math.tau * ((index * 5) % PROXY_LINE_COUNT) / PROXY_LINE_COUNT
    for j in range(count):
        u = max(0.0, head - .023 * (1 - j / max(1, count - 1)))
        yield flow_point(u, angle, phase, rotor_radius)


def update_proxy_objects(scene, *, phase: float, rotor_radius: float = 63.0) -> int:
    """Update existing Demo proxy curves in place and return object count.

    The static scene builder owns topology and materials; this function only
    moves curve points, so a long presentation cannot leak Blender objects.
    """
    import bpy
    updated = 0
    for root in (obj for obj in scene.objects if obj.name.startswith("WFRL.WakeProxy.") and obj.name.endswith(".Volume")):
        turbine_id = root.name.split(".")[2]
        yaw_root = bpy.data.objects.get(f"WFRL.Turbine.{turbine_id}.YawRoot")
        if yaw_root is None:
            continue
        root["fidelity"] = "SYNTH"
        root["provenance"] = "Downstream illustrative tracers; not a solved flow field"
        for index in range(PROXY_LINE_COUNT):
            obj = scene.objects.get(f"WFRL.WakeProxy.{turbine_id}.Line{index}")
            if obj is None or not hasattr(obj.data, "splines") or not obj.data.splines:
                continue
            spline = obj.data.splines[0]
            for point, co in zip(spline.points, proxy_line_points(index, len(spline.points), phase, rotor_radius)):
                point.co = co
            obj["fidelity"] = "SYNTH"
            obj["provenance"] = "Downstream illustrative tracers; not a solved flow field"
            updated += 1
        for index in range(PROXY_RING_COUNT):
            obj = scene.objects.get(f"WFRL.WakeProxy.{turbine_id}.Ring{index}")
            if obj is None:
                continue
            spline = obj.data.splines[0]
            for point, co in zip(spline.points, proxy_ring_points_local(index, len(spline.points), phase, rotor_radius)):
                point.co = co
            updated += 1
        for index in range(PROXY_PULSE_COUNT):
            obj = scene.objects.get(f"WFRL.WakeProxy.{turbine_id}.Pulse{index}")
            if obj is None:
                continue
            spline = obj.data.splines[0]
            for point, co in zip(spline.points, proxy_pulse_points(index, len(spline.points), phase, rotor_radius)):
                point.co = co
            updated += 1
    return updated


def apply_disxy_frame(frame: WakeFrame, *, collection_name: str = "WFRL_Scene"):
    """Create or update one reusable DisXY mesh object in Blender."""
    if frame.kind != "disxy":
        raise WakePayloadError("apply_disxy_frame requires a DisXY frame")
    import bpy
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise WakePayloadError(f"missing Blender collection: {collection_name}")
    vertices, faces = grid_mesh(frame)
    obj = bpy.data.objects.get("WFRL.WakeDisXY")
    if obj is None:
        mesh = bpy.data.meshes.new("WFRL.WakeDisXY.Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new("WFRL.WakeDisXY", mesh)
        collection.objects.link(obj)
        try:
            from .materials import get_material
            obj.data.materials.append(get_material("wake"))
        except Exception:
            pass
    else:
        mesh = obj.data
        if len(mesh.vertices) != len(vertices) or len(mesh.polygons) != len(faces):
            mesh.clear_geometry()
            mesh.from_pydata(vertices, [], faces)
        else:
            for vertex, coordinate in zip(mesh.vertices, vertices):
                vertex.co = coordinate
        mesh.update()
    obj["fidelity"] = frame.fidelity
    obj["provenance"] = frame.provenance
    obj["wake_kind"] = "FAST.Farm DisXY"
    obj["sequence"] = frame.sequence
    obj["validity"] = frame.validity
    obj.hide_render = frame.validity != "valid"
    obj.hide_set(frame.validity != "valid")
    return obj
