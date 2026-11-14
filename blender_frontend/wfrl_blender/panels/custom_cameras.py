"""Shared state, public exports and registration for T1 custom cameras."""
import math
import time
from dataclasses import replace

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from mathutils import Vector

from .. import custom_cameras as core
from .. import custom_camera_history as history, custom_camera_diagnostics as diagnostics
from .. import camera_projection as projection, custom_camera_preview as preview, playback

_ACTIVE = None
_SYNCING = False
_FIELDS = ('x', 'y', 'z', 'yaw', 'pitch', 'roll', 'fov', 'vfov', 'distance', 'focal', 'near', 'far')
_VIEWING = {'WATCH', 'LAYOUT'}


# Camera consumers and callbacks use this namespace for shared state and helpers.
from .custom_camera_overlay import (
    _display_state,
    _redraw_if_changed,
)
from .custom_camera_session import (
    dismiss_for_view_switch,
    session_identity,
    installation_block_reason,
    ViewportSnapshot,
    EditSession,
    cancel_on_load,
    lifecycle_watchdog,
    discard_saved_drafts,
    migrate_loaded,
    status_for,
)
from .custom_camera_properties import (
    _parameters,
    sync_fields,
    fields_changed,
    distance_changed,
    register_properties,
    unregister_properties,
)
from .custom_camera_operators import (
    _timer_advanced,
    WFRL_OT_CustomCamera,
    WFRL_OT_CustomAction,
    WFRL_OT_CustomCoordinates,
)
from .custom_camera_ui import (
    WFRL_PT_CustomCameras,
)


from .custom_camera_output import CLASSES as OUTPUT_CLASSES

from ..native_camera_views import CLASSES as NATIVE_CLASSES

from .stacked_camera_rig import CLASSES as BOX_CLASSES

CLASSES=BOX_CLASSES + NATIVE_CLASSES + OUTPUT_CLASSES + (WFRL_OT_CustomCamera,WFRL_OT_CustomAction,WFRL_OT_CustomCoordinates,WFRL_PT_CustomCameras)
