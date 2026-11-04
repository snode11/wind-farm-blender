"""Track Blender's single native player through its actual scene callbacks.

Screen.is_animation_playing is a global RNA getter: it does not identify the
screen or scene owning playback. Targeted controls must check the PRE callback's
scene before calling Blender's equally global animation_cancel/play operators.
"""

_previous_unregister = globals().get('unregister')
if _previous_unregister:
    _previous_unregister()

_OWNER_SCENE = None
_OWNER_WINDOW = None
_GENERATION = 0


def animation_started(scene, depsgraph=None):
    import bpy
    global _OWNER_SCENE, _OWNER_WINDOW, _GENERATION
    _GENERATION += 1
    _OWNER_SCENE = scene
    _OWNER_WINDOW = getattr(bpy.context, 'window', None)


def animation_stopped(scene, depsgraph=None):
    global _OWNER_SCENE, _OWNER_WINDOW, _GENERATION
    if _OWNER_SCENE == scene:
        _GENERATION += 1
        _OWNER_SCENE = _OWNER_WINDOW = None


def on_load_pre(_unused=None):
    global _OWNER_SCENE, _OWNER_WINDOW, _GENERATION
    _GENERATION += 1
    _OWNER_SCENE = _OWNER_WINDOW = None


def generation():
    """Identify the current start/stop state for deferred scene controls."""
    return _GENERATION


def owner_scene():
    try:
        if _OWNER_SCENE is not None:
            _OWNER_SCENE.as_pointer()
        return _OWNER_SCENE
    except ReferenceError:
        on_load_pre()
        return None


def any_playing():
    """Query the global native state without inferring its owner from a screen."""
    import bpy
    if bpy.app.background:
        return False
    return any(window.screen.is_animation_playing
               for window in bpy.context.window_manager.windows)


def is_playing(scene):
    return owner_scene() == scene and scene is not None and any_playing()


def owner_window():
    import bpy
    windows = list(bpy.context.window_manager.windows)
    try:
        if _OWNER_WINDOW in windows:
            return _OWNER_WINDOW
    except ReferenceError:
        pass
    scene = owner_scene()
    return next((window for window in windows if window.scene == scene), None)


def cancel(scene=None, *, all_scenes=False):
    """Cancel the native player only when the target scene actually owns it."""
    import bpy
    if bpy.app.background:
        return
    if scene is None and not all_scenes:
        scene = bpy.context.scene
    if not any_playing() or (not all_scenes and not is_playing(scene)):
        return
    # Blender has one global native playback timer. A single cancel pauses all
    # views of its owner scene; repeating it per window cannot identify ownership.
    window = owner_window()
    if window is None:
        window = next(iter(bpy.context.window_manager.windows), None)
    if window is not None:
        with bpy.context.temp_override(window=window, screen=window.screen):
            if any_playing() and (all_scenes or is_playing(scene)):
                bpy.ops.screen.animation_cancel(restore_frame=False)


def register():
    import bpy
    from bpy.app.handlers import persistent
    on_load_pre()
    for handlers, callback in ((bpy.app.handlers.animation_playback_pre, animation_started),
                               (bpy.app.handlers.animation_playback_post, animation_stopped),
                               (bpy.app.handlers.load_pre, on_load_pre)):
        persistent(callback)
        if callback not in handlers:
            handlers.append(callback)


def unregister():
    import bpy
    for handlers, callback in ((bpy.app.handlers.animation_playback_pre, animation_started),
                               (bpy.app.handlers.animation_playback_post, animation_stopped),
                               (bpy.app.handlers.load_pre, on_load_pre)):
        if callback in handlers:
            handlers.remove(callback)
    on_load_pre()
