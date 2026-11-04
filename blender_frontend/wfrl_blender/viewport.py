"""Redraw requests for the windows that display a given scene."""

VIEW_3D = frozenset({'VIEW_3D'})


def tag_redraw(scenes=None, *, area_types=VIEW_3D):
    """Tag matching areas in windows showing any of ``scenes``; return the count.

    ``scenes=None`` targets every window. Scenes compare by RNA equality, so a
    fresh wrapper of the same scene still matches. RNA failures propagate so a
    caller's error handling still observes them.
    """
    import bpy
    targets = None if scenes is None else tuple(scenes)
    manager = getattr(getattr(bpy, 'context', None), 'window_manager', None)
    tagged = 0
    for window in tuple(getattr(manager, 'windows', ())):
        if targets is not None and not any(window.scene == target for target in targets):
            continue
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type in area_types:
                area.tag_redraw()
                tagged += 1
    return tagged
