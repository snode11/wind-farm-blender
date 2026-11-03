"""Every native script under tests/blender has a declared way to be run."""
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('wfrl_regression_runner', ROOT / 'scripts/blender/run_regressions.py')
runner = sys.modules.get(SPEC.name)
if runner is None:
    runner = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = runner
    SPEC.loader.exec_module(runner)

# Current usage documents and driver scripts that name a script declare its manual entry point.
# Release check records are historical and do not count.
ENTRY_DOCUMENTS = (
    *(ROOT / '.agents/skills/windfarm-blender-dev/references').glob('*.md'),
    *(ROOT / 'docs/blender').glob('*.md'),
    ROOT / 'wfrl/lidar/README.md',
    *(path for path in (ROOT / 'scripts/blender').glob('*.py') if path.name != 'run_regressions.py'),
)
# Scripts awaiting registration, documentation or removal. Entries only leave this set.
UNTRIAGED = frozenset({
    'blade_recon_inspection_regression.py', 'blade_recon_return_view_regression.py',
    'blade_recon_texture_boundary.py', 'blade_recon_texture_regression.py',
    'box_surface_install_regression.py', 'box_surface_install_window.py', 'box_swivel_regression.py',
    'camera_framing_smoke.py', 'camera_gpu_calibration.py', 'camera_video_regression.py',
    'chart_render_cache_window.py', 'cinematic_clock_regression.py', 'cinematic_motion_regression.py',
    'cinematic_saved_height_regression.py', 'cinematic_smoke.py', 'cinematic_visibility_regression.py',
    'custom_camera_exit_regression.py', 'custom_camera_preview_performance.py',
    'custom_camera_redraw_window.py', 'custom_camera_registration_regression.py',
    'custom_camera_v3_regression.py', 'custom_camera_v3_window.py', 'custom_cameras_regression.py',
    'custom_cameras_window_regression.py', 'demo_presentation_regression.py', 'diagnostics_smoke.py',
    'farm_flex_update_dedup_regression.py', 'farm_split_view_window.py', 'frontend_fixes_window.py',
    'frontend_optimization_smoke.py', 'frontend_runtime_regression.py', 'geometry_smoke.py',
    'gimbal_controls_smoke.py', 'history_export_regression.py', 'history_export_smoke.py',
    'hub_mount_regression.py', 'interactive_options_regression.py', 'launcher_mode_regression.py',
    'live_presentation_smoke.py', 'manual_control_undo_regression.py', 'manual_down_lifecycle_regression.py',
    'manual_down_viewport_regression.py', 'manual_environment_regression.py',
    'manual_native_conflict_regression.py', 'manual_playback_window.py', 'manual_repair_mouse_window.py',
    'manual_video_window_regression.py', 'mappo_demo_regression.py', 'mappo_full_view_default_window.py',
    'mappo_same_source_navigation_window.py', 'mappo_same_source_texture_regression.py',
    'mappo_same_source_texture_reopen.py', 'mappo_same_source_texture_window.py', 'material_reuse_smoke.py',
    'nacelle_revision_smoke.py', 'native_camera_views_regression.py', 'nrel_camera_slots_regression.py',
    'overlay_draw_smoke.py', 'part5_visual_smoke.py', 'part6_presentation_smoke.py',
    'repair_marks_regression.py', 'split_reconstruction_entries_window.py', 'split_reconstruction_window.py',
    'split_texture_sidebar_window.py', 'stacked_cameras_regression.py', 'stacked_cameras_window.py',
    'terrain_sampling_smoke.py', 'three_camera_recommended_window.py', 'three_cameras_window_regression.py',
    'training_stats_smoke.py',
})


def native_scripts():
    return {path.name for path in runner.TEST_ROOT.glob('*.py')}


def registered_scripts():
    return {item.script for item in runner.select_tests(None)}


def manual_entry_points(candidates):
    """Return the scripts in ``candidates`` that have a declared non-runner entry point."""
    texts = [path.read_text(encoding='utf-8') for path in ENTRY_DOCUMENTS]
    return {name for name in candidates if any(name in text for text in texts)}


def test_every_native_script_has_an_entry_point():
    outside = native_scripts() - registered_scripts()
    orphaned = outside - manual_entry_points(outside) - UNTRIAGED
    assert not orphaned, sorted(orphaned)


def test_untriaged_scripts_are_still_orphaned():
    outside = native_scripts() - registered_scripts()
    resolved = UNTRIAGED - (outside - manual_entry_points(outside))
    assert not resolved, f'remove from UNTRIAGED: {sorted(resolved)}'
