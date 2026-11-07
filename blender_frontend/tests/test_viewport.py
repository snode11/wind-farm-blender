"""Scene-scoped redraw requests and drawing-region hit tests."""
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender import viewport


def region(kind, x, y, width, height):
    return NS(type=kind, x=x, y=y, width=width, height=height)


def event(x, y):
    return NS(mouse_x=x, mouse_y=y)


# A 3D view at (100, 50) of 800 x 600 with an overlapping 200 px sidebar on the right
# and a collapsed toolbar on the left.
AREA = NS(regions=[region('HEADER', 100, 626, 800, 24), region('TOOLS', 100, 50, 1, 576),
                   region('UI', 700, 50, 200, 576), region('WINDOW', 100, 50, 800, 600)])


@pytest.mark.parametrize('x, y, inside', [
    (300, 300, True),     # drawing region
    (100, 50, True),      # under the collapsed toolbar
    (750, 300, False),    # overlapping sidebar
    (300, 630, False),    # header
    (950, 300, False),    # another area
    (899, 649, False),    # header still covers the top-right corner
])
def test_window_region_excludes_overlapping_regions(x, y, inside):
    assert viewport.in_window_region(AREA, event(x, y)) is inside


def test_area_without_drawing_region_never_contains_events():
    assert not viewport.in_window_region(NS(regions=[region('UI', 0, 0, 100, 100)]), event(10, 10))


class Scene:
    def __init__(self, pointer):
        self.pointer = pointer

    def as_pointer(self):
        return self.pointer

    def __eq__(self, other):  # Value equality must not make two scenes match.
        return True

    __hash__ = object.__hash__


def window(scene, *types):
    return NS(scene=scene, screen=NS(areas=[NS(type=kind, tag_redraw=Mock()) for kind in types]))


def test_tag_redraw_matches_scenes_by_pointer_and_area_type(monkeypatch):
    a, b = Scene(1), Scene(2)
    windows = [window(a, 'VIEW_3D', 'PROPERTIES'), window(b, 'VIEW_3D'), window(Scene(1), 'VIEW_3D')]
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(window_manager=NS(windows=windows))))
    assert viewport.tag_redraw((a,)) == 2
    assert [area.tag_redraw.call_count for w in windows for area in w.screen.areas] == [1, 0, 0, 1]
    assert viewport.tag_redraw((b,), area_types=None) == 1
    assert viewport.tag_redraw() == 3
