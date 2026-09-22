"""Open the source T1 camera feature without saving a scene or preferences.

Run in a new Blender process with --factory-startup --python <this file>.
Uses the existing recorded demonstration package; never runs a simulator.
"""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]

import wfrl_blender

wfrl_blender.register()
wfrl_blender.load_demo_scene()


def prepare_views():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            area.spaces.active.show_region_ui = True
            for region in area.regions:
                if region.type == 'UI':
                    try:
                        region.active_panel_category = 'View'
                    except AttributeError:
                        pass  # Some Blender builds expose the active tab read-only.
            # Keep the ordinary turbine view on startup. Four-camera comparison
            # is entered explicitly from View; empty slots remain user-owned.
            area.tag_redraw()
            print('WFRL_CUSTOM_CAMERAS_READY', flush=True)
            return


bpy.app.timers.register(prepare_views, first_interval=.5)
