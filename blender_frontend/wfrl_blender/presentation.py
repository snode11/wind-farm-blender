"""Native GPU/BLF presentation overlay; offline values share the Demo sampler."""
_HANDLE = None


def _draw():
    """Viewport presentation cards are retired; controls remain in the sidebar."""
    return


def register_overlay():
    """Remove stale cards on reload without installing a viewport draw handler."""
    unregister_overlay()


def unregister_overlay():
    global _HANDLE
    import bpy
    handle = getattr(bpy, '_wfrl_overlay_handle', _HANDLE)
    if handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    if hasattr(bpy, '_wfrl_overlay_handle'):
        del bpy._wfrl_overlay_handle
    _HANDLE = None


def recording_schedule(fps, duration):
    """Capture wall-clock frames; never seek or advance simulation time."""
    import math
    fps, duration = float(fps), float(duration)
    if not math.isfinite(fps) or not 1 <= fps <= 30:
        raise ValueError('Capture frame rate must be between 1 and 30')
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError('Capture duration must be between 0 and 3600 seconds')
    return 1 / fps, max(1, math.ceil(fps * duration))


def set_presentation_mode(context, enabled):
    """Change workspace chrome without touching runtime or scientific overlays."""
    context.workspace['presentation_mode'] = bool(enabled)
    context.scene['wfrl_presentation_mode'] = bool(enabled)
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                space.show_region_toolbar = not enabled
                space.show_region_tool_header = not enabled
                space.show_gizmo = not enabled
                area.tag_redraw()
