"""Three user-owned T1 observation cameras, expressed in YawRoot local metres.

No module import needs Blender. Camera construction and evaluated surface queries
import bpy lazily. This module never saves files, preferences, or replay state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from . import camera_projection as projection
import hashlib
import json
import math

ROOT_NAME = 'WFRL.Turbine.T1.YawRoot'
SURFACE_NAMES = ('WFRL.Turbine.T1.Nacelle',)
COORDINATE_VERSION = 'T1.YawRoot.local / v1'
MIN_DISTANCE = .02
MAX_DISTANCE = .50
DEFAULT_DISTANCE = .10
DEFAULT_FOV = 75.0
MAX_CUSTOM_CAMERAS = 3
SLOTS = tuple(range(1, MAX_CUSTOM_CAMERAS + 1))
SCHEMA_VERSION = 2
_RETIRED = []
_RETIRED_DATA = []


@dataclass(frozen=True)
class CameraParameters:
    location: tuple[float, float, float]
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    fov: float = DEFAULT_FOV
    vfov: float = projection.DEFAULT_VFOV
    focal_length_mm_record: float | None = None
    output_long_edge_px: int = projection.DEFAULT_LONG_EDGE
    clip_near_m: float = .005
    clip_far_m: float = 30000.


@dataclass(frozen=True)
class SurfaceAnchor:
    point: tuple[float, float, float]
    normal: tuple[float, float, float]
    distance: float
    surface_name: str = SURFACE_NAMES[0]


def _finite(values):
    try:
        return all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) for value in values)
    except (TypeError, ValueError, OverflowError):
        return False


def validate_parameters(params):
    if len(params.location) != 3 or not _finite((*params.location, params.yaw, params.pitch, params.roll, params.fov)):
        raise ValueError('镜头中心和角度必须为有限数值（不接受 NaN 或无穷）')
    if not 0 <= params.yaw < 360:
        raise ValueError('水平角须在 [0, 360)° 范围内')
    if not -90 <= params.pitch <= 90:
        raise ValueError('俯仰角须在 −90–90° 范围内')
    projection.validate(params.fov, params.vfov, params.clip_near_m, params.clip_far_m)
    if params.clip_near_m > MIN_DISTANCE / 2:
        raise ValueError('近裁剪不能超过 0.01 m，以免隐藏紧邻镜头的实体遮挡')
    projection.resolution(params.fov, params.vfov, params.output_long_edge_px)
    if params.focal_length_mm_record is not None:
        projection.finite(params.focal_length_mm_record)
        if params.focal_length_mm_record <= 0:
            raise ValueError('记录焦距必须为正，或留空')
    return params


def validate_distance(distance):
    if not _finite((distance,)) or not MIN_DISTANCE <= distance <= MAX_DISTANCE:
        raise ValueError('镜头离壳距离须在 0.02–0.50 m 范围内')
    return float(distance)


def screen_direction_delta(yaw, pitch, roll, dx, dy):
    """Move optical aim right/up in screen degrees, including arbitrary roll.

    Construct the same camera axes as aim_gimbal, rather than dividing by
    cos(pitch), which becomes singular at the poles. Stored roll is unchanged.
    """
    if not _finite((yaw, pitch, roll, dx, dy)):
        raise ValueError('方向控制必须为有限数值')
    if dx == 0 and dy == 0:
        return yaw, pitch
    y, p, r = map(math.radians, (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    forward = (cp * cy, cp * sy, sp)
    right = (sy, -cy, 0)
    up = (-sp * cy, -sp * sy, cp)
    tx, ty = math.tan(math.radians(dx)), math.tan(math.radians(dy))
    vector = tuple(forward[i] + tx * (cr * right[i] + sr * up[i]) + ty * (-sr * right[i] + cr * up[i]) for i in range(3))
    horizontal = math.hypot(vector[0], vector[1])
    new_yaw = yaw if horizontal < 1e-12 else math.degrees(math.atan2(vector[1], vector[0])) % 360
    return new_yaw, math.degrees(math.atan2(vector[2], horizontal))


def _slot(slot):
    if isinstance(slot, bool) or slot not in SLOTS:
        raise ValueError('自定义相机仅提供 C1–C3')
    return int(slot)


def camera_name(slot):
    return f'WFRL.Camera.T1.Custom.{_slot(slot)}'


def place_on_surface(camera, surface, distance=DEFAULT_DISTANCE):
    distance = validate_distance(distance)
    surface = replace(surface, distance=distance)
    location = tuple(surface.point[i] + surface.normal[i] * distance for i in range(3))
    nx, ny, nz = surface.normal
    yaw = math.degrees(math.atan2(ny, nx)) % 360
    pitch = math.degrees(math.atan2(nz, math.hypot(nx, ny)))
    camera['custom_mount_mode'] = 'SURFACE'
    return apply_parameters(camera, replace(parameters(camera), location=location, yaw=yaw, pitch=pitch, roll=0), surface)


def set_distance(camera, distance):
    surface = anchor(camera)
    if surface is None:
        raise ValueError('请先点击机舱外壳安装')
    distance = validate_distance(distance)
    location = tuple(surface.point[i] + surface.normal[i] * distance for i in range(3))
    return apply_parameters(camera, replace(parameters(camera), location=location), replace(surface, distance=distance))


# Public imports and runtime helper lookups share this namespace.
from .custom_camera_state import (
    is_available,
    _root,
    get_camera,
    migrate_camera,
    migrate_scene,
    parameters,
    anchor,
    apply_parameters,
    aspect_ratio,
    apply_research,
    enabled_cameras,
    validate_native_state,
)
from .custom_camera_geometry import (
    _surface_geometry,
    _geometry,
    _inside,
    validate_position,
    raycast_surface,
    model_signature,
    surface_names,
    _closed_mesh,
    validate_research_position,
    model_identity,
)
from .custom_camera_layout import (
    copy_text,
    layout_dict,
    layout_hash,
    validate_layout,
    live_layout_hash,
    config_equal,
    _summary_value,
    layout_summary,
    restore_layout,
    import_layout,
)
from .custom_camera_lifecycle import (
    begin_draft,
    _remove,
    cancel_draft,
    commit_draft,
    clear_slot,
    set_enabled,
    _publication_checkpoint,
    _refresh_references,
    _remember_retired,
    _cleanup_retired_data,
    cleanup_retired,
    _publish,
)
