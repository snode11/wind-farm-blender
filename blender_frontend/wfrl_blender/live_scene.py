"""Validate backend geometry before applying live channels to scene objects."""
import math
from .scene_model import SceneDTO


def snapshot_scene(payload):
    if not isinstance(payload.get('scene'), dict):
        raise ValueError('Backend snapshot requires scene geometry metadata')
    scene = SceneDTO.from_mapping(payload['scene'])
    ids = [t.turbine_id for t in scene.turbines]
    if len(ids) > 64 or len(set(ids)) != len(ids) or any(not tid for tid in ids):
        raise ValueError('Live scene requires 1–64 unique turbine IDs')
    if scene.turbine_model.lower() != 'nrel5mw':
        raise ValueError('Live geometry currently supports nrel5mw only')
    numbers = [scene.dt_s, scene.wind_speed_mps, scene.wind_direction_deg]
    numbers += [value for turbine in scene.turbines for value in (turbine.x_m, turbine.y_m)]
    if not all(math.isfinite(value) for value in numbers) or scene.dt_s <= 0:
        raise ValueError('Scene coordinates and time step must be finite')
    if set(ids) != {t['turbine_id'] for t in payload['turbines']}:
        raise ValueError('Snapshot turbine IDs do not match scene geometry')
    return scene


def build_live_scene(scene):
    import bpy
    from . import _cancel_playback
    from .scene_builder import build_scene
    from .cameras import build_cameras
    _cancel_playback()
    build_scene(scene)
    build_cameras(scene)
    from . import atmosphere
    display_scene = bpy.context.scene
    atmosphere.apply_preset(display_scene, "clear", enabled=True, quality="realtime")
    # Configure the viewport without switching workspaces, camera, or run state.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                space.clip_start = 1.0
                space.clip_end = 10000
                space.overlay.show_overlays = False
                space.shading.type = 'RENDERED'
    display_scene['wfrl_proxy_note'] = 'SYNTH: illustrative wake, not FAST.Farm wind data'
    bpy.context.scene["wfrl_scene_kind"] = "live"
    for obj in bpy.data.objects:
        if obj.name.startswith('WFRL.Turbine.'):
            obj.animation_data_clear()
        if obj.name.startswith('WFRL.WakeProxy.'):
            visible = getattr(display_scene, 'wfrl_show_wake', True) and not obj.name.endswith('.Volume')
            obj.hide_render = not visible
            obj.hide_set(not visible)
            obj['fidelity'] = 'SYNTH'
        if obj.name.startswith('WFRL.Fixture.'):
            obj.hide_render = True
            obj.hide_set(True)
    # A snapshot can rebuild while paused, so the animation timer cannot be
    # relied on to restore the selected layer on its next running tick.
    from . import _update_layers
    _update_layers(display_scene)


def animate_illustrative_wake(scene, dt, *, running):
    """Wall-clock decoration only; never advance a backend or its timestamps."""
    if scene is None or scene.get('wfrl_scene_kind') != 'live' or not running:
        return
    if not getattr(scene, 'wfrl_show_wake', True):
        return
    from .wake import update_proxy_objects
    # Geometry is sampled analytically, not numerically stepped. Dropping a
    # slow frame's elapsed time would make the preview cycle machine-dependent.
    phase = float(scene.get('wfrl_proxy_phase', 0.0)) + max(dt, 0.0) * 0.9
    scene['wfrl_proxy_phase'] = phase
    update_proxy_objects(scene, phase=phase)
