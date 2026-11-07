"""Prepare a screenshot view of the verified real 200-frame reconstruction.
Run in a NEW Blender scene/process, for example from its Python Console:
 import runpy; runpy.run_path('/absolute/path/to/open_for_screenshot.py', run_name='__main__')
The original viewer clears/recreates this scene. No rendering or saving is done.
The frame-change callback stays active; timeline is left paused at frame 101.
"""
import json
import runpy
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[4]
COMPAT = ROOT / 'out/trial-20261004/blender/compat/blender_view_compat.py'
RECON = ROOT / 'out/trial-20261004/synth20/recon.json'
TRUTH = ROOT / 'data/synth20/truth.json'
CAMERAS = ROOT / 'data/synth20/cameras.json'
old_argv = sys.argv[:]
try:
    sys.argv = [str(COMPAT), '--', '--recon', str(RECON),
                '--truth', str(TRUTH), '--cameras', str(CAMERAS)]
    runpy.run_path(str(COMPAT), run_name='__main__')
finally:
    sys.argv = old_argv

scene = bpy.context.scene
scene.frame_set(101)
bpy.context.view_layer.update()

# Keep the scene's calibrated cameras and true meshes, but hide their viewport clutter.
for ob in bpy.data.objects:
    ob.select_set(False)
    if ob.type == 'CAMERA' or ob.name.startswith('Truth_Blade'):
        ob.hide_set(True)
    if ob.name.startswith('Recon_Blade'):
        ob.hide_set(False)
        if 'evidence' in ob.data.color_attributes:
            ob.data.color_attributes.active_color_name = 'evidence'
        for polygon in ob.data.polygons:
            polygon.use_smooth = True
bpy.context.view_layer.objects.active = None

layout = bpy.data.workspaces.get('Layout')
views = []
for window in bpy.context.window_manager.windows:
    if layout is not None:
        window.workspace = layout
    for area in window.screen.areas:
        # The root may call this from a Python Console that used to be the main view.
        # Convert that main console back to VIEW_3D for the final screenshot.
        if area.type == 'CONSOLE':
            area.type = 'VIEW_3D'
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        space.lens = 45
        space.clip_start = 0.1
        space.clip_end = 2000
        shade = space.shading
        shade.type = 'SOLID'
        try:
            shade.color_type = 'VERTEX'
            color_mode = 'SOLID / VERTEX'
        except (TypeError, ValueError):
            # Material preview evaluates the original evidence VertexColor node.
            shade.type = 'MATERIAL'
            shade.use_scene_world = False
            shade.use_scene_lights = False
            color_mode = 'MATERIAL preview / original evidence node'
        if shade.type == 'SOLID':
            shade.light = 'STUDIO'
            shade.background_type = 'VIEWPORT'
            shade.background_color = (0.92, 0.92, 0.92)
            shade.show_shadows = True
            shade.show_cavity = True
            shade.cavity_type = 'BOTH'
            shade.show_object_outline = False
        space.overlay.show_overlays = False
        # Looking mostly along +X toward the rotor, with modest sideways/up angle.
        # All of the ~150 m tower-and-rotor height fits with room around the tips.
        region = space.region_3d
        region.view_location = Vector((3.0, 0.0, 75.0))
        region.view_rotation = Vector((1.0, -0.38, 0.16)).to_track_quat('Z', 'Y')
        region.view_perspective = 'ORTHO'
        region.view_distance = 235.0
        area.tag_redraw()
        views.append({'workspace': window.workspace.name,
                      'color_mode': color_mode,
                      'view_location': list(region.view_location),
                      'view_distance': region.view_distance,
                      'perspective': region.view_perspective})

print('[blade_recon screenshot] ' + json.dumps({
    'recon': str(RECON), 'recon_frames': len(json.loads(RECON.read_text())['frames']),
    'frame': scene.frame_current, 'time_seconds': 10.0,
    'fps': scene.render.fps, 'animation_auto_started': False,
    'frame_change_pre_handlers': [getattr(fn, '__name__', str(fn))
                                 for fn in bpy.app.handlers.frame_change_pre],
    'selected_objects': [ob.name for ob in bpy.context.selected_objects],
    'cameras_viewport_hidden': [ob.name for ob in bpy.data.objects
                                if ob.type == 'CAMERA' and ob.hide_get()],
    'truth_ghosts_viewport_hidden': True, 'views': views,
    'saved_or_rendered': False,
}, ensure_ascii=False))
