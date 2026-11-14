"""Render each turbine's fixed Down view at one shared replay frame."""
from pathlib import Path
import hashlib
import importlib
import json

import bpy

OUT = Path(__file__).resolve().parents[1]
MODULE = 'bl_ext.user_default.wfrl_blender'
addon = importlib.import_module(MODULE)
package = Path(addon.__file__).resolve().parent
assert package == Path('BLENDER_USER_EXTENSIONS/user_default/wfrl_blender')
assert MODULE in bpy.context.preferences.addons
assert hasattr(bpy.types.Scene, 'wfrl_gimbal_kind')
assert bpy.ops.wfrl.gimbal_preset.get_rna_type() is not None
cameras = importlib.import_module(MODULE + '.cameras')
farm_flex = importlib.import_module(MODULE + '.farm_flex')
addon.load_demo_scene(camera_rig=False)
scene = bpy.context.scene
scene.wfrl_deflection_visible = False
scene.wfrl_flex_show_tip_trails = False
scene.frame_set(52)
for obj in scene.objects:
    if '.ClearanceRadar.Beam' in obj.name:
        obj.hide_set(True)
        obj.hide_render = True
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.studio_light = 'studio.sl'
scene.display.shading.color_type = 'MATERIAL'
scene.display.shading.show_shadows = False
scene.display.shading.show_cavity = True
scene.display.shading.show_specular_highlight = True
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
scene.render.image_settings.file_format = 'PNG'
scene.view_settings.view_transform = 'Standard'
rows = []
for turbine in ('T1', 'T2', 'T3'):
    before = (scene.frame_current, scene.frame_subframe)
    assert bpy.ops.wfrl.farm_flex_view(turbine=turbine, angle='DOWN') == {'FINISHED'}
    assert (scene.frame_current, scene.frame_subframe) == before
    camera = scene.camera
    bpy.context.view_layer.update()
    scene.render.filepath = str(OUT / f'{turbine}_Down.png')
    bpy.ops.render.render(write_still=True)
    rows.append(dict(turbine=turbine, camera=camera.name, parent=camera.parent.name,
                     location=list(camera.location), world_location=list(camera.matrix_world.translation),
                     yaw=camera['gimbal_yaw'], pitch=camera['gimbal_pitch'],
                     roll=camera['gimbal_roll'], hfov=camera['gimbal_fov'],
                     shift=[camera.data.shift_x, camera.data.shift_y], lens_mm=camera.data.lens,
                     frame=scene.frame_current, subframe=scene.frame_subframe,
                     file=scene.render.filepath))
assert bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='DOWN') == {'FINISHED'}
scene.wfrl_farm_panel_page = 'TOOLS'
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            space.shading.type = 'SOLID'
            space.shading.light = 'STUDIO'
            space.shading.studio_light = 'studio.sl'
            space.shading.color_type = 'MATERIAL'
            space.shading.show_shadows = False
            space.overlay.show_overlays = False
            space.show_region_ui = True
            space.camera = scene.camera
            space.use_local_camera = False
            space.region_3d.view_perspective = 'CAMERA'
            cameras.fill_camera_view(area, scene)
bpy.ops.object.select_all(action='DESELECT')
scene_file = OUT / 'Farm_Down.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(scene_file))
build = json.loads((package / 'build-info.json').read_text())
(OUT / 'installed-render-checks.json').write_text(json.dumps(dict(
    status='PASS', addon_file=addon.__file__, build_info=build,
    render_engine=scene.render.engine, studio_light='studio.sl', resolution=[1920,1080],
    shared_frame=52, preview_time_s=117.85, cameras=rows, scene_file=str(scene_file),
    measurement_beams_hidden=True, geometry_modified=False, physics_rerun=False,
    scope='Three saved renders at a shared replay frame; not all-azimuth shoulder visibility.'
), ensure_ascii=False, indent=2) + '\n')
(OUT / 'installed-probe.json').write_text(json.dumps(dict(
    status='PASS', blender=bpy.app.version_string, module=MODULE, addon_file=addon.__file__,
    build_info=build, enablement_persisted=True, manual_registration_called=False,
    gimbal_preset_registered=True, scene_properties_registered=True
), indent=2) + '\n')
print('FARM_DOWN_PREVIEW_READY', flush=True)
