"""Public entry points for manual Down cameras, motion and MP4 export.

Implementation lives in the camera, motion, job, rendering and UI modules.
Exports are direct references, including the registered callbacks and classes.
"""
from .manual_down_cameras import (
    DOWN_CAMERA_REVISION, MANUAL_DOWN_PITCH_DEG, MANUAL_DOWN_REVISION, manual_down_pitch,
    TRIPLE_CAMERA_FOV_DEG, TRIPLE_OVERLAP_DEG, TRIPLE_STEP_DEG, LABEL_DEPTH, LABEL_SPAN,
    BLADE_COLORS, _entry, _camera_name, ensure_down_camera, _side_camera_name, _shaft_frame,
    side_camera_matrix, ensure_side_cameras, _down_cameras, _label_material, _overlay_object,
    _text, _plate, _frame_tangents, _ensure_labels, _stations, _world, _set_hidden, current_blade,
    _LABEL_PARTS, update_labels, _labels_listener, _labels_update, apply_blade_model,
    _reaim_down_cameras, _apply_pending_blade_model, _blade_model_update,
)
from .manual_down_motion import (
    SHAKE_MAX_DEG, SHAKE_RPM_REF, SHAKE_TIP_MARGIN_DEG, _SHAKE_TERMS, _SHAKE_3P_WEIGHT,
    shake_signal, shake_amplitude, _shake_shape, _tip_local, min_shake_tilt, apply_shake,
    _start_shake, _end_shake, _SHAKE_BASE, _SHAKE_POSE, _flat, rest_matrix, _unshake,
    _scene_cameras, live_shake_update, live_shake_stop, _shake_before_save, _shake_after_restore,
    _shake_toggle, _SHAKE_HANDLERS, _shake_sidecar, _plan, pose_at, apply_frame, apply_motion,
    _start_motion, _end_motion,
)
from .manual_down_render import (
    VIDEO_SIZE, VIDEO_SIZES, VIEWPORT_ENGINES, ENGINES, _GPU_RELEASE_FRAMES, _release_gpu,
    gpu_renderer, is_integrated_gpu, ffmpeg_executable, _ENCODER_TIMEOUT_S, _ENCODER_EXIT_GRACE_S,
    _ENCODER_LOG_TAIL, _encoder_error, _reap_encoders, _offscreen_encoder, _draw_offscreen,
    _draw_offscreen_multi, _view_area, _prepare_view, _restore_view,
)
from .manual_down_jobs import (
    _JOB_KEY, _begin_samples, _end_samples, _append_frame, _frame_handler, _write_frame_handler,
    _sidecar, _SAVED_RENDER, _SAVED_IMAGE, _SAVED_FFMPEG, _snapshot, _restore_marker_cameras,
    _restore, _configure_video, _ExportOwnershipLost, _owned_scene, _check_owner,
    _ensure_cleanup_timer, _own_job, _restore_manual_pose, _job_error, _restore_job, _finish,
    _retry_exports, _before_scene_change, _end_render, _render_complete, _render_cancel,
    _mp4_target, _has_animation, _validate_export, export_triple_down_video, export_down_video,
    _LIFECYCLE_HANDLERS,
)
from .manual_down_ui import (
    _TURBINES, _turbine_default, WFRL_OT_ManualDownView, _ENGINE_ITEMS, WFRL_OT_ManualDownVideo,
    WFRL_OT_ManualTripleDownVideo, CLASSES, _BLADE_ITEMS, register, unregister,
)
