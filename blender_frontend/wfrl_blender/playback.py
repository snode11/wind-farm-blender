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


def animation_started(scene, depsgraph=None):
    import bpy
    global _OWNER_SCENE, _OWNER_WINDOW
    _OWNER_SCENE = scene
    _OWNER_WINDOW = getattr(bpy.context, 'window', None)


def animation_stopped(scene, depsgraph=None):
    global _OWNER_SCENE, _OWNER_WINDOW
    if _OWNER_SCENE == scene:
        _OWNER_SCENE = _OWNER_WINDOW = None


def on_load_pre(_unused=None):
    global _OWNER_SCENE, _OWNER_WINDOW
    _OWNER_SCENE = _OWNER_WINDOW = None


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
