"""Redraw requests for the windows that display a given scene."""

VIEW_3D = frozenset({'VIEW_3D'})


def tag_redraw(scenes=None, *, area_types=VIEW_3D):
    """Tag matching areas in windows showing any of ``scenes``; return the count.

    ``scenes=None`` targets every window and ``area_types=None`` every area.
    Scenes match by RNA pointer, so a fresh wrapper of the same scene still
    matches. RNA failures propagate so a caller's error handling observes them.
    """
    import bpy
    targets = None if scenes is None else {_key(scene) for scene in scenes}
    manager = getattr(getattr(bpy, 'context', None), 'window_manager', None)
    tagged = 0
    for window in tuple(getattr(manager, 'windows', ())):
        if targets is not None and _key(window.scene) not in targets:
            continue
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area_types is None or area.type in area_types:
                area.tag_redraw()
                tagged += 1
    return tagged


def _key(scene):
    return scene.as_pointer() if hasattr(scene, 'as_pointer') else id(scene)


def in_window_region(area, event):
    """Whether a window-space event lies in the area's drawing region.

    Overlapping sidebars, toolbars and headers belong to their own regions, so a
    modal operator returns those clicks to the interface instead of using them.
    """
    def contains(region):
        return (region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height)
    drawing = None
    for region in area.regions:
        if region.type == 'WINDOW':
            drawing = region
        elif region.width > 1 and contains(region):
            return False
    return drawing is not None and contains(drawing)
