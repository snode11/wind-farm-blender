"""NREL six-morphology editor bound to the native recorded flexible replay."""
import copy
from dataclasses import asdict
import json
import math
import time
from pathlib import Path
import bpy
import numpy as np
from bpy.app.handlers import persistent
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .reference_surface import ReferenceSurface
from .defect_schema import KIND_DESCRIPTIONS, KIND_LABELS, PRESETS, new_document, new_defect, validate_document, save_document
from .defect_shapes import lightning_crater
from .defect_store import DefectStore
from .defect_geometry import DefectGeometry

_SESSIONS = {}
_INITIALIZING = {}
_UPDATING = False
DOCUMENT_KEY = 'wfrl_nrel_defect_document'


def session(context=None):
    return _SESSIONS.get((context or bpy.context).scene.as_pointer())


from .editor_geometry import (
    GeometrySet as GeometrySet,
)
from .editor_session import (
    EditorSession as EditorSession,
    ensure as ensure,
    after_update as after_update,
    detach as detach,
    sync_defect_list as sync_defect_list,
    props_from_defect as props_from_defect,
    defect_from_props as defect_from_props,
    draft_from_props as draft_from_props,
    preview_is_current as preview_is_current,
)
from .editor_persistence import (
    restore_saved as restore_saved,
    save_pre as save_pre,
    restore_after_save as restore_after_save,
    save_post as save_post,
    save_post_fail as save_post_fail,
)
from .editor_ui import (
    redraw_editor as redraw_editor,
    helper_changed as helper_changed,
    NRELDefectListItem as NRELDefectListItem,
    NREL_UL_defects as NREL_UL_defects,
    NRELDefectProperties as NRELDefectProperties,
    wrapped_lines as wrapped_lines,
    text_columns as text_columns,
    wrapped_label as wrapped_label,
    status_box as status_box,
    NREL_PT_defects as NREL_PT_defects,
)
from .editor_operators import (
    action_unavailable as action_unavailable,
    ACTION_MESSAGES as ACTION_MESSAGES,
    ACTION_HELP as ACTION_HELP,
    NREL_OT_defect_action as NREL_OT_defect_action,
    NREL_OT_defect_pick as NREL_OT_defect_pick,
    NREL_OT_defect_status as NREL_OT_defect_status,
    NREL_OT_defect_scan as NREL_OT_defect_scan,
)


@persistent
def frame_changed(scene,depsgraph=None):
    s=_SESSIONS.get(scene.as_pointer())
    if s is not None and not getattr(s, 'closing', False) and not _UPDATING and (s.draft is not None or s.busy):
        frozen = s.edit_state[0] if s.draft is not None else s.freeze_time
        if abs(float(scene.get('defect_time_s',0))-frozen)>1e-8: s.set_time(frozen)


CLASSES=(NRELDefectListItem,NRELDefectProperties,NREL_UL_defects,
         NREL_OT_defect_action,NREL_OT_defect_pick,NREL_OT_defect_scan,
         NREL_OT_defect_status,NREL_PT_defects)


def register_properties():
    bpy.types.Scene.wfrl_nrel_defects=bpy.props.PointerProperty(type=NRELDefectProperties)
    for handlers,fn in ((bpy.app.handlers.save_pre,save_pre),
                        (bpy.app.handlers.save_post,save_post),
                        (bpy.app.handlers.save_post_fail,save_post_fail),
                        (bpy.app.handlers.frame_change_post,frame_changed)):
        if fn not in handlers: handlers.append(fn)


def unregister_properties():
    detach()
    for handlers,fn in ((bpy.app.handlers.save_pre,save_pre),
                        (bpy.app.handlers.save_post,save_post),
                        (bpy.app.handlers.save_post_fail,save_post_fail),
                        (bpy.app.handlers.frame_change_post,frame_changed)):
        if fn in handlers: handlers.remove(fn)
    if hasattr(bpy.types.Scene,'wfrl_nrel_defects'): del bpy.types.Scene.wfrl_nrel_defects
