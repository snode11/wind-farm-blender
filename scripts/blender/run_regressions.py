"""Run the source-runtime Blender regression set in isolated serial processes.

Use --list for each script's window, package, output and timeout contract.
--output must be a new directory. Final results require the script's completion
marker and JSON evidence as well as a clean process exit. Console-only scripts
use the bootstrap's completion JSON. Visible tests require their own final JSON;
returning from a script that merely registers a timer is not completion.

Examples::

    python3 scripts/blender/run_regressions.py --list
    python3 scripts/blender/run_regressions.py --output /path/to/new-results
    python3 scripts/blender/run_regressions.py --tests shared_context_switch_regression.py \
        native_camera_playback_regression.py --output /path/to/new-window-results

Saved scenes and reports stay under --output. Isolated profiles and temporary
files stay under a unique --scratch-root child, listed in result.json for the
caller to retain or clean. This runner does not build or install an extension.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


if __name__ == '__main__':
    sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from wfrl.blender_bridge.formal_process import OwnedProcesses

TEST_ROOT = ROOT / 'blender_frontend/tests/blender'
ADDON_ROOT = ROOT / 'blender_frontend/wfrl_blender'


@dataclass(frozen=True)
class Regression:
    script: str
    marker: str
    report: str | None = 'checks.json'
    output_kind: str = 'directory'
    mode: str = 'background'
    timeout_s: float = 300
    package: str = 'builtin_mappo'
    required_flags: tuple[str, ...] = ()
    phases: tuple[str, ...] = ('run',)
    scope: str = 'Native background data checks; visible interaction not verified.'
    expected_exceptions: tuple[tuple[str, int], ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


REGRESSIONS = (
    Regression('extension_lifecycle_smoke.py', 'WFRL_EXTENSION_LIFECYCLE_SMOKE=PASS', None,
               scope='Native partial-registration rollback, repeat unload and saved-result transport.'),
    Regression('manual_pose_smoke.py', 'WFRL_MANUAL_POSE_SMOKE=PASS', None,
               scope='Independent paused manual scene and strict JSON pose contract.'),
    Regression('camera_pinhole_projection_regression.py', 'CAMERA_PINHOLE_PROJECTION_PASS', None,
               package='synthetic', scope='Formal RGB, G descriptor and A native pixel-aspect projection equivalence.'),
    Regression('synthetic_texture_rollback_regression.py', 'SYNTHETIC_TEXTURE_ROLLBACK_PASS', 'result.json',
               package='synthetic', scope='Native synthetic/surface allocation rollback, packed RGB and retryable resource ownership.'),
    Regression('resource_lifecycle_contract_regression.py', 'RESOURCE_LIFECYCLE_CONTRACT_PASS', 'result.json',
               scope='Shared Worlds, deleted RNA cleanup, native load and GW184 multi-resolution projection.'),
    Regression('defect_commit_cleanup_regression.py', 'DEFECT_COMMIT_CLEANUP_PASS', 'result.json', timeout_s=300,
               scope='Native NREL/GW184 editor commit, restore, undo/redo and retryable disposal.'),
    Regression('camera_postprocessing_contract_regression.py', 'CAMERA_POSTPROCESSING_CONTRACT_PASS', 'result.json',
               scope='Small native camera PNG pixels, VSE/compositor/FileOutput isolation and restoration.'),
    Regression('radar_recording_stop_regression.py', 'RADAR_RECORDING_STOP_PASS', 'result.json',
               scope='Nonblocking local recorder stop, retained ownership, unload and drain completion; no hardware.'),
    Regression('nrel_editor_resource_regression.py', 'NREL_EDITOR_RESOURCE_PASS', 'checks.json',
               scope='Native helper deletion, normal editor detach and retryable resource cleanup.'),
    Regression('manual_down_copy_regression.py', 'MANUAL_DOWN_COPY_PASS', 'checks.json',
               scope='Manual Down camera ownership in independent scene copies and native saved files.'),
    Regression('camera_export_contract_regression.py', 'CAMERA_EXPORT_CONTRACT_PASS', 'result.json',
               scope='Down marker isolation, actual-camera publication gates, decoded video and excluded annotations.'),
    Regression('model_environment_integration.py', 'MODEL_ENVIRONMENT_INTEGRATION_PASS', 'checks.json', timeout_s=300,
               scope='Model and World isolation across manual scenes, replay, export copies and native saved files.'),
    Regression('camera_failure_recovery_regression.py', 'CAMERA_FAILURE_RECOVERY_PASS', 'result.json',
               package='synthetic'),
    Regression('custom_camera_retirement_regression.py', 'CUSTOM_CAMERA_RETIREMENT',
               'retirement-validation.json', package='synthetic'),
    Regression('chart_scene_ownership_regression.py', 'CHART_SCENE_OWNERSHIP_PASS', 'result.json'),
    Regression('imbalance_regression.py', 'IMBALANCE_REGRESSION_PASS', 'result.json',
               package='builtin_mappo_then_dual',
               scope='Native response analysis identity, result layers and replay seeks.'),
    Regression('mode_scene_ownership_regression.py', 'MODE_SCENE_OWNERSHIP_PASS', 'result.json',
               package='synthetic', scope='Native mode changes preserve independent scene data.'),
    Regression('video_compositor_regression.py', 'VIDEO_COMPOSITOR_PASS', 'result.json', timeout_s=240),
    Regression('home_entry_smoke.py', 'HOME_ENTRY_SMOKE_PASS', None,
               scope='Native RNA and entry operations with a layout adapter; visible layout and FPS not verified.'),
    Regression('manual_control_regression.py', 'MANUAL_CONTROL_REGRESSION_PASS', timeout_s=600),
    Regression('manual_control_state_regression.py', 'MANUAL_CONTROL_STATE_PASS'),
    Regression('manual_rate_pitch_regression.py', 'MANUAL_RATE_PITCH_PASS', 'result.json'),
    Regression('manual_rotor_conflict_regression.py', 'MANUAL_ROTOR_CONFLICT_PASS'),
    Regression('manual_pitch_trial_regression.py', 'MANUAL_PITCH_TRIAL_PASS'),
    Regression('manual_pitch_trial_undo_regression.py', 'MANUAL_PITCH_TRIAL_UNDO_PASS'),
    Regression('manual_video_generator_preflight_regression.py', 'MANUAL_VIDEO_GENERATOR_PREFLIGHT_PASS',
               'result.json', package='builtin_rootkink',
               scope='Native generator preparation and frozen trial; no rendering or media output.'),
    Regression('manual_video_launch_regression.py', 'MANUAL_VIDEO_LAUNCH_PASS',
               timeout_s=660, package='builtin_rootkink',
               scope='Native supervised short MP4 export, source scene isolation and cancellation.'),
    Regression('manual_pose_undo_regression.py', 'MANUAL_POSE_UNDO_PASS'),
    Regression('manual_scene_recovery_regression.py', 'MANUAL_SCENE_RECOVERY_PASS', timeout_s=600),
    Regression('manual_scene_link_compatibility_regression.py', 'MANUAL_SCENE_LINK_COMPATIBILITY_PASS'),
    Regression('frontend_sessions_window.py', 'FRONTEND_SESSIONS_WINDOW_PASS', 'result.json',
               mode='visible', package='synthetic',
               scope='Native window screenshots, recording cleanup and scene ownership; scripted controls.'),
    Regression('animation_render_regression.py', 'ANIMATION_RENDER_REGRESSION_PASS', 'result.json',
               timeout_s=240, package='synthetic_then_builtin_mappo',
               scope='Native short PNG renders, cancellation and source preservation; not full-clip FPS.'),
    Regression('animation_replay_restore_regression.py', 'ANIMATION_REPLAY_RESTORE_PASS', 'result.json',
               package='unavailable_standalone_replay',
               scope='Native saved-scene replay recovery must succeed before rendering.'),
    Regression('farm_reopen_settings_regression.py', 'FARM_REOPEN_SETTINGS_PASS', 'result.json',
               scope='Native FarmFlex save/reopen preserves saved mapping, output settings and source identity.'),
    Regression('radar_x100_regression.py', 'X100_NATIVE_PASS', 'result.json',
               scope='Native radar source, recording and shared collection lifecycle; no device or GPU proof.'),
    Regression('radar_x100_edges.py', 'X100_EDGES_PASS', 'result.json',
               scope='Native radar capture guards, clock, visibility and shared object ownership.'),
    Regression('radar_x100_history_regression.py', 'X100_HISTORY_PASS', 'result.json',
               scope='Native undo/redo, FarmFlex rebinding and recording termination; no GPU drawing.'),
    Regression('radar_x100_analysis.py', 'X100_ANALYSIS_PASS', 'result.json',
               package='builtin_mappo_and_dual_beam',
               scope='Native same-frame sidewall diagnostics, NumPy oracle and source validation; no GPU or device proof.'),
    Regression('radar_x100_mount_presets.py', 'X100_MOUNT_PRESETS_PASS', 'result.json',
               scope='Native single-sensor mount presets, clock preservation and rollback; no GPU drawing.'),
    Regression('radar_x100_configuration.py', 'X100_CONFIGURATION_PASS', 'result.json',
               scope='Native RNA configuration, source declarations and display controls; no GPU drawing.'),
    Regression('radar_x100_detection_mode.py', 'X100_DETECTION_MODE_PASS', 'result.json',
               scope='Native observation modes, timeline sampling, recording, replay and save/reopen; no GPU or device proof.'),
    Regression('radar_x100_clearance.py', 'X100_CLEARANCE_PASS', 'result.json',
               scope='Detection-only clearance intervals, echo classes, observation-only inputs, recording, playback and save/reopen; no GPU or device proof.'),
    Regression('radar_x100_observations.py', 'X100_OBSERVATIONS_PASS', 'result.json',
               scope='Area-sampled observations, source-bound installation search import and observation-only blade estimates.'),
    Regression('radar_x100_estimation_window.py', 'X100_ESTIMATION_WINDOW_PASS', 'result.json', mode='visible',
               scope='Actual GPU model-tip pixels and scripted toggle withdrawal; no external input or field-accuracy proof.'),
    Regression('radar_x100_binding_replay.py', 'X100_BINDING_REPLAY_PASS', 'result.json',
               scope='Native source bindings, recording order and independent replay clocks; no GPU drawing.'),
    Regression('farm_capture_transaction_regression.py', 'FARM_CAPTURE_TRANSACTION_REGRESSION_PASS', 'result.json',
               package='synthetic',
               scope='Native RNA capture ownership with replay/image/playback adapters; no GPU pixels or native playback.'),
    Regression('split_mappo_texture_rollback_regression.py', 'SPLIT_MAPPO_TEXTURE_ROLLBACK_PASS', 'result.json',
               timeout_s=600,
               scope='Native same-source allocation rollback, external ownership and import retry; no rendered pixels.'),
    Regression('farm_flex_regression.py', 'FARM_FLEX_SWITCH_RANGE_LIFECYCLE_PASS'),
    Regression('farm_flex_workload_regression.py', 'FARM_FLEX_WORKLOAD_REGRESSION_PASS', None),
    Regression('tip_deflection_regression.py', 'TIP_DEFLECTION_PASS', 'checks.json', 'file',
               required_flags=('software_checks', 'physical_gate')),
    Regression('tip_revolution_regression.py', 'TIP_REVOLUTION_PASS', None, package='synthetic'),
    Regression('tip_trail_comparison_regression.py', 'TIP_TRAIL_COMPARISON_PASS', 'trail-checks.json'),
    Regression('tower_flex_regression.py', 'TOWER_FLEX_PASS', 'checks.json', 'file'),
    Regression('prebend_regression.py', 'PREBEND_PASS', timeout_s=900),
    Regression('split_turbine_context_regression.py', 'SPLIT_TURBINE_CONTEXT_NATIVE_PASS',
               'shared-context-validation.json', package='builtin_split_example'),
    Regression('nrel_editor_playback_regression.py', 'NREL_EDITOR_PLAYBACK_PASS', mode='visible',
               scope='Scripted native-window playback ownership; external keyboard input not verified.'),
    Regression('nrel_defect_editor_regression.py', 'NREL_DEFECT_EDITOR_{phase}_PASS',
               '{phase}-checks.json', phases=('save', 'reopen')),
    Regression('clearance_replay_smoke.py', 'CLEARANCE_REPLAY_SMOKE_PASS', 'checks.json', 'file',
               package='builtin_mappo_then_synthetic',
               scope='Synthetic reader and public scene-loader lifecycle; package files and physics not verified.'),
    Regression('replay_history_regression.py', 'REPLAY_HISTORY_PASS'),
    Regression('native_camera_playback_regression.py', 'NATIVE_CAMERA_PLAYBACK_PASS',
               'result.json', mode='visible',
               scope='Scripted native-window camera playback; external Space events and FPS not verified.'),
    Regression('scene_playback_lifecycle_regression.py', 'SCENE_PLAYBACK_LIFECYCLE_PASS'),
    Regression('native_camera_proxy_regression.py', 'NATIVE_CAMERA_PROXY_PASS', package='synthetic',
               scope='Real Blender camera resources and evaluated constraints with a viewport adapter; no pixels/FPS.'),
    Regression('farm_down_mount_regression.py', 'FARM_DOWN_MOUNT_PASS'),
    Regression('t1_down_mount_regression.py', 'T1_DOWN_MOUNT_PASS'),
    Regression('rebuild_mesh_regression.py', 'REBUILD_MESH_REGRESSION=PASS'),
    Regression('dual_beam_regression.py', 'DUAL_BEAM_NATIVE_PASS', 'checks.json', 'file',
               package='builtin_dual_beam'),
    Regression('radar_feedback_regression.py', 'RADAR_FEEDBACK_PASS', 'checks.json', 'file',
               scope='Native background materials, events and replay; visible icon rendering not verified.'),
    Regression('custom_camera_capture_regression.py', 'CUSTOM_CAMERA_CAPTURE_NATIVE_PASS',
               'result.json', package='synthetic',
               scope='Native camera/configuration guards; GPU capture not verified.'),
    Regression('custom_camera_load_invalidation_regression.py',
               'CUSTOM_CAMERA_LOAD_INVALIDATION_REGRESSION_PASS', package='synthetic',
               scope='Native file loads, invalidation callbacks and refresh requests with a render adapter; GPU pixels not verified.'),
    Regression('custom_camera_shared_object_regression.py',
               'CUSTOM_CAMERA_SHARED_OBJECT_REGRESSION_PASS', package='synthetic',
               scope='Native scene ownership, camera objects/data and transactional rejection; GPU drawing not verified.'),
    Regression('connection_recovery_regression.py', 'CONNECTION_RECOVERY_NATIVE_PASS', package='synthetic',
               scope='Native RNA, confirmed recovery action and runtime/transport over socketpair; modal UI and real backend not verified.'),
    Regression('three_cameras_regression.py', 'THREE_CAMERAS_NATIVE_PASS', None, package='synthetic'),
    Regression('farm_front_view_regression.py', 'FARM_FRONT_VIEW_PASS'),
    Regression('shared_context_switch_regression.py', 'SHARED_CONTEXT_SWITCH_PASS', mode='visible',
               package='builtin_split_example',
               scope='Scripted native-window model switches, rollback and save/reopen.'),
    Regression('tower_fittings_regression.py', 'TOWER_FITTINGS_PASS', 'checks.json', 'file'),
    Regression('connection_smoke.py', 'WFRL_CONNECTION_SMOKE=PASS', None,
               scope='Native runtime over a loopback TCP peer: handshake lock, disconnect, reload and refused-port fallback.'),
    Regression('wake_placement_smoke.py', 'WFRL_WAKE_PLACEMENT=PASS', None,
               scope='Illustrative wake proxies at the yaw-root hub frame, above ground, downstream, and display toggles.'),
    Regression('cinematic_live_regression.py', 'CINEMATIC_LIVE_REGRESSION=PASS', None,
               scope='Cinematic wake layer survives a live-scene rebuild with the scientific proxies hidden.'),
    Regression('blade_recon_inspection_regression.py', 'BLADE_RECON_INSPECTION_CREATE_PASS', None,
               scope='Blade reconstruction appearance inspection, create phase; reopen phases not run.'),
    Regression('blade_recon_return_view_regression.py', 'BLADE_RECON_RETURN_VIEW_CREATE_PASS', None,
               scope='Independent texture inspection return view, create phase; reopen phase not run.'),
    Regression('blade_recon_texture_boundary.py', 'BLADE_RECON_TEXTURE_NATIVE_BOUNDARY_PASS', None,
               scope='Cycles shader tip/root clamp and periodic seam.'),
    Regression('blade_recon_texture_regression.py', 'BLADE_RECON_TEXTURE_CREATE_PASS', None,
               scope='Independent reconstruction scene and packed textures, create phase; reopen phase not run.'),
    Regression('camera_framing_smoke.py', 'WFRL_CAMERA_FRAMING=PASS', None,
               scope='Overview cameras contain full rotor envelopes.'),
    Regression('camera_video_regression.py', 'CAMERA_VIDEO_REGRESSION', None,
               scope='Camera video source clock, fixed mount, clean rays and resources; media output not inspected.'),
    Regression('cinematic_clock_regression.py', 'CINEMATIC_CLOCK_REGRESSION=PASS', None,
               scope='Slow UI ticks do not stretch the cinematic preview cycle.'),
    Regression('cinematic_motion_regression.py', 'CINEMATIC_MOTION_REGRESSION=PASS', None,
               scope='Cinematic tracers move continuously in world space through wind changes.'),
    Regression('cinematic_saved_height_regression.py', 'SAVED_HEIGHT_REGRESSION=PASS', None,
               scope='Loaded turbine geometry anchors the cinematic wake.'),
    Regression('cinematic_smoke.py', 'WFRL_CINEMATIC_SMOKE=PASS', None,
               scope='Independent cinematic geometry and wind/yaw following.'),
    Regression('cinematic_visibility_regression.py', 'CINEMATIC_VISIBILITY_REGRESSION=PASS', None,
               scope='Cinematic layer playback visibility contract.'),
    Regression('custom_camera_registration_regression.py', 'CUSTOM_CAMERA_REGISTRATION_PASS', None,
               scope='Extension registration does not read scenes in restricted context.'),
    Regression('custom_camera_history_regression.py', 'CUSTOM_CAMERA_HISTORY_PASS', None,
               scope='Custom camera transactional history; GPU and window behaviour not verified.'),
    Regression('custom_cameras_regression.py', 'CUSTOM_CAMERAS_PASS', None,
               scope='T1 custom camera contracts without modifying delivered files.'),
    Regression('diagnostics_smoke.py', 'WFRL_DIAGNOSTICS_SMOKE=PASS', None,
               scope='Diagnostics registration and lossless Text Editor output.'),
    Regression('farm_flex_update_dedup_regression.py', 'FARM_FLEX_UPDATE_DEDUP_PASS', None,
               scope='Same-time flex updates are deduplicated with unchanged numerical results.'),
    Regression('frontend_runtime_regression.py', 'FRONTEND_RUNTIME_PASS', None,
               scope='Recorded-flex numerical, visibility and seek checks.'),
    Regression('geometry_smoke.py', 'WFRL_GEOMETRY_SMOKE=PASS', None,
               scope='Source-derived turbine scale and independent pitch/azimuth axes.'),
    Regression('history_export_regression.py', 'HISTORY_EXPORT_REGRESSION_PASS', None,
               scope='History export scene ownership and extension registration.'),
    Regression('history_export_smoke.py', 'WFRL_HISTORY_EXPORT_SMOKE=PASS', None,
               scope='Bounded history export through the visible operator.'),
    Regression('interactive_options_regression.py', 'INTERACTIVE_OPTIONS_REGRESSION_PASS', None,
               scope='Interactive launch options and RNA registration; no solver started.'),
    Regression('live_presentation_smoke.py', 'LIVE_WAKE_ANIMATION_PAUSE_PASS', None,
               scope='Live wake animation pauses with the scene.'),
    Regression('manual_control_undo_regression.py', 'MANUAL_CONTROL_UNDO_PASS', None,
               scope='Undo boundaries for manual-scene creation, entry and recovery.'),
    Regression('manual_down_lifecycle_regression.py', 'MANUAL_DOWN_LIFECYCLE_PASS', None,
               scope='Down view rejection preserves output settings and teardown.'),
    Regression('manual_environment_regression.py', 'MANUAL_ENVIRONMENT_PASS', None,
               scope='Manual daylight presentation, private assets and return shading.'),
    Regression('manual_native_conflict_regression.py', 'MANUAL_NATIVE_CONFLICT_PASS', None,
               scope='Manual trial and fixed Down reject competing native controls.'),
    Regression('material_reuse_smoke.py', 'WFRL_MATERIAL_REUSE_SMOKE=PASS', None,
               scope='Scene rebuilds reuse materials.'),
    Regression('nacelle_revision_smoke.py', 'WFRL_NACELLE_REVISION_SMOKE=PASS', None,
               scope='Nacelle topology, fitting visibility and yaw attachment.'),
    Regression('nrel_camera_slots_regression.py', 'NREL_CAMERA_SLOTS_PASS', None,
               scope='Missing native camera slots keep identity and reject incomplete layouts.'),
    Regression('scene_resource_budget_smoke.py', 'SCENE_RESOURCE_BUDGET_SMOKE=PASS', None,
               scope='Nonstandard turbine IDs, hidden-object updates, shared geometry and grass budget.'),
    Regression('scene_visual_layers_smoke.py', 'WFRL_SCENE_VISUAL_LAYERS_SMOKE=PASS', None,
               scope='Atmosphere presets, ground cover layout, fixture metadata and proxy reuse.'),
    Regression('camera_targeting_smoke.py', 'CAMERA_TARGETING_SMOKE=PASS', None,
               scope='Camera targeting and capture operator registration.'),
    Regression('stacked_cameras_regression.py', 'STACKED_CAMERAS_PASS', None,
               scope='Three-camera reference coverage, occlusion and rigid pose.'),
    Regression('terrain_sampling_smoke.py', 'TERRAIN_SAMPLING_PASS', None,
               scope='Cached terrain queries match mesh ray casts.'),
    Regression('training_stats_smoke.py', 'WFRL_TRAINING_STATS_SMOKE=PASS', None,
               scope='Formal-training statistics presentation.'),
    Regression('box_surface_install_regression.py', 'BOX_SURFACE_INSTALL_PASS', 'validation.json',
               scope='Nacelle support contact, standoff and rigid optics of the camera box.'),
    Regression('box_swivel_regression.py', 'BOX_SWIVEL_PASS', 'swivel-validation.json',
               scope='Support base stays fixed while the housing and cameras swivel.'),
    Regression('hub_mount_regression.py', 'HUB_MOUNT_REGRESSION=PASS', None,
               scope='NREL hub height and sensor/camera/wake mount rigs.'),
    Regression('mappo_demo_regression.py', 'MAPPO_COMPLETE_DEMO_PASS', None,
               scope='MAPPO demo transport, telemetry, saved replay and preflight rejection of a missing package.'),
    Regression('mappo_same_source_texture_regression.py', 'MAPPO_SAME_SOURCE_TEXTURE_NATIVE_PASS', 'native-validation.json',
               scope='601-sample MAPPO same-source texture data checks.'),
    Regression('repair_marks_regression.py', 'DEFAULT_B1_REPAIR_PASS', 'validation.json',
               scope='Default repair mark, rigid/flexible attachment and load lifecycle.'),
    Regression('box_surface_install_window.py', 'BOX_SURFACE_WINDOW_PASS', 'window-validation.json', mode='visible',
               scope='Scripted native-window camera box installation.'),
    Regression('camera_gpu_calibration.py', 'GPU_CALIBRATION_PASS', 'result.json', mode='visible',
               scope='Actual GPU corner, colour and roll calibration.'),
    Regression('chart_render_cache_window.py', 'CHART_RENDER_CACHE_WINDOW_PASS', 'result.json', mode='visible',
               scope='Native chart render cache and window ownership.'),
    Regression('custom_camera_exit_regression.py', 'CUSTOM_CAMERA_EXIT_PASS', 'result.json', mode='visible',
               scope='Leaving the custom-camera overlay restores the scene and timeline.'),
    Regression('custom_camera_preview_performance.py', 'CUSTOM_CAMERA_PREVIEW_PERFORMANCE_PASS', 'result.json', mode='visible',
               scope='Preview GPU ownership and geometry invalidation; timings are not FPS acceptance.'),
    Regression('demo_presentation_regression.py', 'DEMO_PRESENTATION_PASS', 'result.json', mode='visible',
               scope='Equal-column layout, capture quality isolation and restoration.'),
    Regression('farm_split_view_window.py', 'FARM_SPLIT_PASS', 'result.json', mode='visible',
               scope='Synchronized T1 side-by-side views.'),
    Regression('frontend_fixes_window.py', 'FRONTEND_FIXES_WINDOW_PASS', 'window-checks.json', mode='visible',
               scope='Diagnostics identity and transport isolation from another playing window.'),
    Regression('manual_down_viewport_regression.py', 'MANUAL_DOWN_VIEWPORT', 'result.json', mode='visible',
               scope='GPU Down export preserves the source and restores the viewport.'),
    Regression('manual_playback_window.py', 'MANUAL_PLAYBACK_WINDOW_PASS', 'checks.json', mode='visible',
               scope='Native player ownership across manual-scene round trips; mouse input not verified.'),
    Regression('manual_video_window_regression.py', 'MANUAL_VIDEO_WINDOW_PASS', 'checks.json', mode='visible',
               scope='Manual video progress, completed output and cancellation in a native window.'),
    Regression('native_camera_views_regression.py', 'NATIVE_PASS', 'check.json', mode='visible',
               scope='Native camera view areas advance and draw during playback.'),
    Regression('overlay_draw_smoke.py', 'OVERLAY_DRAW_PASS', 'result.json', mode='visible',
               scope='Chart and presentation POST_PIXEL callbacks draw without GPU errors.'),
    Regression('stacked_cameras_window.py', 'STACKED_WINDOW_PASS', 'window-validation.json', mode='visible', environment=(('WFRL_TEST_QUIT', '1'),),
               scope='Three-camera capture and housing inspection in a native window.'),
    Regression('three_camera_recommended_window.py', 'RECOMMENDED_WINDOW_PASS', 'window-validation.json', mode='visible', environment=(('WFRL_TEST_QUIT', '1'), ('WFRL_CHECK_DEFAULT_ONLY', '1')),
               scope='Bundled default three-camera layout captured and shown in native views.'),
    Regression('three_cameras_window_regression.py', 'THREE_CAMERAS_WINDOW_PASS', 'result.json', mode='visible',
               scope='Three-camera GPU views, export, replay and lifecycle; external input not verified.'),
)


BOOTSTRAP = '''import json, os, runpy, sys, traceback
from pathlib import Path
import bpy
script, mode, destination, root = sys.argv[sys.argv.index('--') + 1:]
sys.dont_write_bytecode = True
sys.path[:0] = [str(Path(root) / 'blender_frontend'), root]
record = dict(status='RUNNING', final=False, script=script,
              background=bpy.app.background, blender=bpy.app.version_string,
              startup_dirty=bpy.data.is_dirty)
def save():
    Path(destination).write_text(json.dumps(record, indent=2) + '\\n', encoding='utf-8')
save()
try:
    assert bpy.app.background == (mode == 'background'), 'Unexpected execution mode'
    if hasattr(bpy.context.preferences.filepaths, 'use_save_on_exit'):
        bpy.context.preferences.filepaths.use_save_on_exit = False
    bpy.context.preferences.filepaths.temporary_directory = os.environ['TMPDIR']
    runpy.run_path(script, run_name='__main__')
    addon = sys.modules.get('wfrl_blender')
    assert addon is not None, 'Expected source module was not imported'
    record['addon_file'] = str(Path(addon.__file__).resolve())
    assert Path(record['addon_file']).parent == Path(root) / 'blender_frontend/wfrl_blender'
    record.update(returned_normally=True, final=(mode == 'background'),
                  post_script_dirty=bpy.data.is_dirty,
                  status='PASS' if mode == 'background' else 'WAITING_FOR_CALLBACKS')
    save()
except BaseException:
    record.update(status='FAIL', final=True, returned_normally=False, error=traceback.format_exc())
    save()
    raise RuntimeError('Regression script did not return normally: ' + script)
'''


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {'status': 'INVALID_JSON', 'error': 'Expected an object'}
    except (OSError, ValueError) as exc:
        return {'status': 'MISSING_OR_INVALID_JSON', 'error': str(exc)}


def source_hashes(script):
    paths = sorted(ADDON_ROOT.rglob('*.py')) + [script, TEST_ROOT / '_native.py',
                                                ROOT / 'scripts/blender/open_farm_flex.py', Path(__file__)]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def package_inputs():
    inputs = {name: {'path': str(ADDON_ROOT / 'assets' / directory),
                   'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()
                   if manifest.is_file() else None}
            for name, directory in (('builtin_mappo', 'mappo'), ('builtin_dual_beam', 'dual_beam'))
            for manifest in [ADDON_ROOT / 'assets' / directory / 'manifest.json']}
    example = ADDON_ROOT / 'assets/examples/mappo_reconstruction_split.blend'
    inputs['builtin_split_example'] = dict(path=str(example),
        sha256=hashlib.sha256(example.read_bytes()).hexdigest() if example.is_file() else None)
    return inputs


def select_tests(names):
    by_name = {item.script: item for item in REGRESSIONS}
    names = list(by_name) if names is None else names
    if not names:
        raise ValueError('The regression selection is empty')
    names = [name if name.endswith('.py') else name + '.py' for name in names]
    unknown = sorted(set(names) - by_name.keys())
    if unknown:
        raise ValueError('Unknown regression scripts: ' + ', '.join(unknown))
    if len(names) != len(set(names)):
        raise ValueError('Duplicate regression scripts are not allowed')
    return [by_name[name] for name in names]


def isolated_environment(scratch, output, phase, inherited=None):
    inherited = os.environ if inherited is None else inherited
    env = {key: value for key, value in inherited.items()
           if not key.startswith(('WFRL_', 'BLENDER_USER_', 'PYTHON'))}
    for variable, name in (('BLENDER_USER_CONFIG', 'config'), ('BLENDER_USER_SCRIPTS', 'scripts'),
                           ('BLENDER_USER_DATAFILES', 'datafiles'), ('TMPDIR', 'tmp'),
                           ('XDG_CACHE_HOME', 'cache')):
        directory = scratch / name
        directory.mkdir(parents=True, exist_ok=False)
        env[variable] = str(directory)
    env.update(TMP=env['TMPDIR'], TEMP=env['TMPDIR'], PYTHONNOUSERSITE='1',
               PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
               WFRL_ADDON_MODULE='wfrl_blender', WFRL_EXPECTED_ADDON_ROOT=str(ADDON_ROOT),
               WFRL_FARM_FLEX_PACKAGE=str(ADDON_ROOT / 'assets/mappo'),
               WFRL_DUAL_PACKAGE=str(ADDON_ROOT / 'assets/dual_beam'),
               WFRL_TEST_OUTPUT=str(output), WFRL_FARM_FLEX_EVIDENCE=str(output))
    if phase in ('save', 'reopen'):
        env['WFRL_NREL_DEFECT_PHASE'] = phase
    return env


def traceback_exceptions(log):
    """Read rendered Python diagnostics, excluding prose and escaped JSON text."""
    header = re.compile(r'^(?:(?:Error: |ERROR: )?Python: )?Traceback \(most recent call last\):\s*$')
    group_header = re.compile(r'^(?:(?:Error: |ERROR: )?Python: )? *\+ Exception Group Traceback \(most recent call last\):\s*$')
    frame = re.compile(r'^\s+File ".+", line \d+(?:, in .+)?$')
    summary = re.compile(r'^[\w.]+(?::.*)?$')
    lines, found = log.splitlines(), []
    for index, line in enumerate(lines):
        if group_header.fullmatch(line):
            summaries, has_frame = [], False
            for following in lines[index + 1:]:
                if not following.startswith(' '):
                    break
                content = re.sub(r'^(?:[ \t]*[|+])+(?: )?', '', following)
                has_frame = has_frame or bool(frame.fullmatch(content))
                if summary.fullmatch(content):
                    summaries.append(content)
            if has_frame:
                found.extend(summaries or ['<incomplete exception group traceback>'])
            continue
        if not header.fullmatch(line):
            continue
        has_frame = False
        for following in lines[index + 1:]:
            if frame.fullmatch(following):
                has_frame = True
            elif following.startswith((' ', '\t')) or not following:
                continue
            else:
                if has_frame:
                    found.append(following if summary.fullmatch(following) else '<incomplete traceback>')
                break
        else:
            if has_frame:
                found.append('<incomplete traceback>')
    return found


def evaluate(spec, phase, returncode, timed_out, execution, report, log, source_changed=False):
    """A zero exit cannot stand in for final assertions or asynchronous completion."""
    failures = []
    exceptions = Counter(traceback_exceptions(log))
    expected = Counter(dict(spec.expected_exceptions))
    if exceptions != expected:
        failures.append(f'Python exception diagnostics: actual={dict(exceptions)}, expected={dict(expected)}')
    if timed_out:
        failures.append('Process exceeded its timeout')
    if returncode != 0:
        failures.append(f'Process exit code: {returncode}')
    if source_changed:
        failures.append('Source changed during execution; rerun against a stable checkout')
    expected_execution = 'PASS' if spec.mode == 'background' else 'WAITING_FOR_CALLBACKS'
    if (execution.get('status') != expected_execution or not execution.get('returned_normally')
            or execution.get('background') is not (spec.mode == 'background')
            or (spec.mode == 'background' and execution.get('final') is not True)):
        failures.append('Bootstrap did not confirm the expected script execution')
    if report is not None:
        status = report.get('status')
        if status in ('SKIP', 'NOT_RUN') and not failures:
            return status, [report.get('reason', 'Script reported incomplete coverage')]
        if status != 'PASS' and not (status is None and spec.required_flags):
            failures.append(f'Script JSON status: {status}')
        if 'final' in report and report['final'] is not True:
            failures.append('Script JSON is not final')
        for flag in spec.required_flags:
            if report.get(flag) != 'PASS':
                failures.append(f'{flag}: {report.get(flag)}')
        if report.get('physical_gate', 'PASS') != 'PASS':
            failures.append('Physical acceptance gate did not pass')
        checks = report.get('checks', {})
        if isinstance(checks, dict):
            failures.extend('Failed check: ' + name for name, value in checks.items()
                            if isinstance(value, dict) and value.get('status') == 'FAIL')
    elif spec.report is not None or spec.mode == 'visible':
        failures.append('Missing final script JSON')
    marker = spec.marker.format(phase=phase.upper())
    if not any(line == marker or line.startswith(marker + ' ') for line in log.splitlines()):
        failures.append('Missing final completion marker: ' + marker)
    return ('FAIL', failures) if failures else ('PASS', [])


def stop_process(process, owner, *, grace=8.):
    """Reap the launcher only after all identified descendants have stopped."""
    for signum in (signal.SIGTERM, getattr(signal, 'SIGKILL', None)):
        if signum is None:
            process.kill()
        else:
            owner.signal(signum)
        deadline = time.monotonic() + grace
        while owner.alive() and time.monotonic() < deadline:
            time.sleep(.02)
        if not owner.alive():
            process.wait(timeout=grace)
            return
    raise RuntimeError('Regression process cleanup unconfirmed')


def run_invocation(spec, phase, args, case, scratch, bootstrap):
    run = case / phase
    run.mkdir()
    output = case / ('checks.json' if spec.output_kind == 'file' else 'artifacts')
    environment = isolated_environment(scratch, output, phase)
    environment.update(spec.environment)
    script = TEST_ROOT / spec.script
    execution_path = run / 'execution.json'
    report_path = (output if spec.output_kind == 'file' else output / spec.report.format(phase=phase)) if spec.report else None
    command = [str(args.blender)] + (['--background'] if spec.mode == 'background' else ['--no-window-focus'])
    command += ['--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                '--python', str(bootstrap), '--', str(script), spec.mode, str(execution_path), str(ROOT)]
    before = source_hashes(script)
    write_json(run / 'source-before.json', before)
    write_json(run / 'launch.json', dict(command=command, mode=spec.mode, output=str(output),
               report=str(report_path) if report_path else str(execution_path),
               environment={key: value for key, value in environment.items()
                            if key.startswith(('WFRL_', 'BLENDER_USER_')) or key in ('TMPDIR', 'XDG_CACHE_HOME')}))
    started = time.monotonic()
    timed_out, interrupted, launch_error = False, False, None
    returncode = None
    with (run / 'blender.log').open('w', encoding='utf-8') as log:
        try:
            owner = OwnedProcesses(env_key='WFRL_REGRESSION_RUN_TOKEN')
            process = owner.start(command, cwd=ROOT, env=environment, stdout=log,
                                  stderr=subprocess.STDOUT)
            try:
                returncode = process.wait(timeout=args.timeout or spec.timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                stop_process(process, owner)
                returncode = process.returncode
            except KeyboardInterrupt:
                interrupted = True
                stop_process(process, owner)
                returncode = process.returncode
            if owner.alive():
                stop_process(process, owner)
                launch_error = 'Regression left live descendants after its launcher exited'
        except (OSError, RuntimeError) as exc:
            launch_error = str(exc)
    after = source_hashes(script)
    write_json(run / 'source-after.json', after)
    execution = read_json(execution_path)
    report = read_json(report_path) if report_path else None
    status, reasons = evaluate(spec, phase, returncode, timed_out, execution, report,
                               (run / 'blender.log').read_text(errors='replace', encoding='utf-8'), before != after)
    if interrupted or launch_error:
        status = 'FAIL'
        reasons.append('Interrupted by caller' if interrupted else launch_error)
    result = dict(status=status, phase=phase, reasons=reasons, returncode=returncode,
                  timed_out=timed_out, interrupted=interrupted, duration_s=time.monotonic() - started,
                  source_changed=before != after, execution=execution, script_result=report,
                  log=str(run / 'blender.log'), report=str(report_path) if report_path else None)
    write_json(run / 'result.json', result)
    return result


def aggregate(statuses):
    if any(status == 'FAIL' for status in statuses):
        return 'FAIL'
    return 'PASS' if statuses and all(status == 'PASS' for status in statuses) else 'NOT_RUN'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--blender', type=Path, default=os.environ.get('WFRL_BLENDER_BIN') or
                        shutil.which('blender') or '/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--scratch-root', type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument('--tests', nargs='+', help='Script names or stems; defaults to all registered tests')
    parser.add_argument('--test-list', type=Path, help='File containing whitespace-separated script names')
    parser.add_argument('--mode', choices=('all', 'background', 'visible'), default='all')
    parser.add_argument('--timeout', type=float, help='Per-process timeout override in seconds')
    parser.add_argument('--list', action='store_true', help='Print execution contracts without launching Blender')
    args = parser.parse_args(argv)
    if args.tests and args.test_list:
        parser.error('Use --tests or --test-list, not both')
    try:
        selected = select_tests(args.test_list.read_text(encoding='utf-8').split() if args.test_list else args.tests)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    packages = package_inputs()
    if args.list:
        print(json.dumps(dict(tests=[asdict(spec) for spec in selected], package_inputs=packages), indent=2))
        return 0
    if args.output is None or (args.timeout is not None and args.timeout <= 0):
        parser.error('--output is required and --timeout must be positive')
    args.output = args.output.expanduser().resolve()
    args.blender = args.blender.expanduser().resolve()
    if not args.blender.is_file():
        parser.error('Blender executable does not exist: ' + str(args.blender))
    if args.output.exists():
        parser.error('Output must be a new directory: ' + str(args.output))
    args.output.mkdir(parents=True)
    args.scratch_root = args.scratch_root.expanduser().resolve()
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='wfrl-regressions-', dir=args.scratch_root))
    bootstrap = scratch / 'bootstrap.py'
    bootstrap.write_text(BOOTSTRAP, encoding='utf-8')
    summary = dict(status='RUNNING', final=False, verification_layer='SOURCE_RUNTIME',
                   blender=str(args.blender), package_inputs=packages, tests=[],
                   temporary_paths=[str(scratch)], visible_processes='serial, independent factory-startup profiles')
    stopped = False
    for spec in selected:
        case = args.output / Path(spec.script).stem
        record = dict(script=spec.script, mode=spec.mode, scope=spec.scope, invocations=[])
        if stopped:
            record.update(status='NOT_RUN', reason='Batch interrupted')
        elif args.mode != 'all' and args.mode != spec.mode:
            record.update(status='SKIP', reason='Excluded by explicit --mode ' + args.mode)
        elif spec.mode == 'visible' and sys.platform.startswith('linux') and not any(
                os.environ.get(key) for key in ('DISPLAY', 'WAYLAND_DISPLAY')):
            record.update(status='NOT_RUN', reason='No visible desktop display is available')
        else:
            case.mkdir()
            for phase in spec.phases:
                if record['invocations'] and record['invocations'][-1]['status'] != 'PASS':
                    record['invocations'].append(dict(phase=phase, status='NOT_RUN', reason='Required save did not pass'))
                    continue
                print(f'RUN {spec.script} [{spec.mode}/{phase}]', flush=True)
                result = run_invocation(spec, phase, args, case, scratch / case.name / phase, bootstrap)
                record['invocations'].append(result)
                stopped = stopped or result['interrupted']
            record['status'] = aggregate([item['status'] for item in record['invocations']])
        summary['tests'].append(record)
        print(record['status'] + ' ' + spec.script, flush=True)
        write_json(args.output / 'result.json', summary)
    summary.update(status=aggregate([row['status'] for row in summary['tests']]), final=True,
                   counts=dict(Counter(row['status'] for row in summary['tests'])))
    write_json(args.output / 'result.json', summary)
    print(json.dumps({'status': summary['status'], 'counts': summary['counts'], 'result': str(args.output / 'result.json')}))
    return 0 if summary['status'] == 'PASS' else 1 if summary['status'] == 'FAIL' else 2


if __name__ == '__main__':
    raise SystemExit(main())
