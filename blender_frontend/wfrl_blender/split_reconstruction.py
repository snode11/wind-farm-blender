"""Native lifecycle and framing for saved MAPPO/reconstruction comparisons.

The reconstruction's samples and source cameras are never edited here.  A small
timer observes screen/region changes; camera ownership is enforced by the view
operators, not by polling and repairing an overwritten camera.
"""
from __future__ import annotations

import math
import time
import json


_SESSIONS = {}
_JOBS = {}
_BOUNDS = {}
_REGISTERED = False
_INTERVAL = .25
_DEBOUNCE = .35
_METADATA = {
    'split_reconstruction_start_s': 117.,
    'split_reconstruction_timeline_fps': 60.,
    'split_reconstruction_stride': 6,
    'split_reconstruction_samples': 601,
    'split_reconstruction_frame_start': 1,
}


def _legacy_available(scene):
    return bool(scene and scene.get('split_reconstruction_review') and
                scene.objects.get('SplitRecon.Camera') is not None and
                all(scene.objects.get(f'SplitRecon.B{b}') is not None for b in (1, 2, 3)))


def ensure_geometry_structure(scene):
    """Reuse source T1 supports in the saved reconstruction coordinate frame."""
    if not _legacy_available(scene):
        return []
    from . import split_turbine_context
    anchor = scene.objects['SplitRecon.B1']
    return split_turbine_context.ensure(scene, prefix='SplitRecon.Reference.',
        offset=tuple(anchor.matrix_world.translation),
        names_key='split_reconstruction_structure_names')


def available(scene):
    """Offer surface comparison in a saved example or loaded MAPPO scene."""
    return bool(_legacy_available(scene) or (scene and scene.get('wfrl_farm_flex_path') and
                scene.objects.get('WFRL.Turbine.T1.Rotor') is not None))


def reconstruction_camera(scene):
    return texture_adapter(scene).camera(scene) or scene.objects.get('SplitRecon.Camera')


def texture_adapter(scene):
    """Use the complete turbine and the saved MAPPO source clock."""
    from . import split_mappo_texture
    return split_mappo_texture


def enabled(scene):
    return (available(scene) and scene.get('split_texture_current_source') != 'SYNTHETIC'
            and not (scene.get('split_texture_review') and not scene.get('split_texture_current_source'))
            and bool(scene.get('split_review_enabled', _legacy_available(scene))))


def is_reconstruction_view(scene, space):
    """Identify the saved right view even while it is manually orbited."""
    return bool(enabled(scene) and getattr(space, 'type', None) == 'VIEW_3D' and
                getattr(space, 'camera', None) == reconstruction_camera(scene))


_DISPLAY_OPTICS = ('type', 'lens', 'ortho_scale', 'sensor_width', 'sensor_height',
                   'sensor_fit', 'shift_x', 'shift_y', 'clip_start', 'clip_end')


from .split_reconstruction_views import (
    _views as _views,
    _pair as _pair,
    _key as _key,
    _region as _region,
    _rectangle as _rectangle,
    _signature as _signature,
    _navigation as _navigation,
    _restore_navigation as _restore_navigation,
    _presentation as _presentation,
    _restore_presentation as _restore_presentation,
    _source_camera as _source_camera,
    _leave_local as _leave_local,
    _isolate as _isolate,
    _configure as _configure,
    _sidebar_category as _sidebar_category,
    _layout_factor as _layout_factor,
    _timeline_all as _timeline_all,
)

from .split_reconstruction_framing import (
    _corners as _corners,
    _saved_corners as _saved_corners,
    _points as _points,
    _project as _project,
    _fit_camera as _fit_camera,
    _perspective_distance as _perspective_distance,
    _fit_free as _fit_free,
    _fit_pair as _fit_pair,
)

from .split_reconstruction_modes import (
    _display_origin as _display_origin,
    _transfer_display_camera as _transfer_display_camera,
    switch_mode as switch_mode,
    _saved_return_navigation as _saved_return_navigation,
    enter as enter,
    fit as fit,
    exit_mode as exit_mode,
)

from .split_reconstruction_lifecycle import (
    _remember as _remember,
    _defer_resize_fit as _defer_resize_fit,
    _set_error as _set_error,
    _redraw_scene as _redraw_scene,
    _advance_jobs as _advance_jobs,
    _tick as _tick,
    _ensure_timer as _ensure_timer,
    _clear_research_texture_runtime as _clear_research_texture_runtime,
    _load_pre as _load_pre,
    _load_post as _load_post,
    _save_pre as _save_pre,
    _history_post as _history_post,
    _depsgraph_post as _depsgraph_post,
)


def _callbacks():
    import bpy
    from . import split_mappo_texture
    return ((bpy.app.handlers.load_pre, _load_pre),
            (bpy.app.handlers.load_post, _load_post),
            (bpy.app.handlers.save_pre, _save_pre),
            (bpy.app.handlers.undo_post, _history_post),
            (bpy.app.handlers.redo_post, _history_post),
            (bpy.app.handlers.depsgraph_update_post, _depsgraph_post),
            (bpy.app.handlers.frame_change_post, split_mappo_texture.frame_changed))


def register():
    import bpy
    from .split_reconstruction_lifecycle import _register_callbacks
    global _REGISTERED
    previous = _REGISTERED
    _REGISTERED = True
    try:
        with _register_callbacks(_callbacks(), None if bpy.app.background else _tick, first_interval=.1):
            pass
    except Exception:
        _REGISTERED = previous
        raise


def unregister():
    from .split_reconstruction_lifecycle import _remove_callbacks
    global _REGISTERED
    _REGISTERED = False
    errors = _remove_callbacks(_callbacks(), _tick)
    try:
        _load_pre(None)
    except Exception as exc:
        errors.append(exc)
    if errors:
        raise errors[0]
