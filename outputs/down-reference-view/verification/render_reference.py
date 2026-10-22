"""Render a nacelle-mounted view of the rootkink blade at saved off-tower poses."""
from pathlib import Path
import hashlib
import importlib
import json
import math

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

OUT = Path(__file__).resolve().parents[1]
MODULE = 'bl_ext.user_default.wfrl_blender'
addon = importlib.import_module(MODULE)
PACKAGE = Path(addon.__file__).resolve().parent
EXPECTED = Path('BLENDER_USER_EXTENSIONS/user_default/wfrl_blender')
assert PACKAGE == EXPECTED and MODULE in bpy.context.preferences.addons
farm = importlib.import_module(MODULE + '.farm_flex')
cameras = importlib.import_module(MODULE + '.cameras')
geometry = importlib.import_module(MODULE + '.turbine_geometry')
addon.load_demo_scene()
scene = bpy.context.scene
scene.wfrl_blade_model = 'rootkink'
assert not scene.get('wfrl_blade_model_error')
active = farm.active_for(scene)
assert active.model == 'rootkink'
scene.wfrl_deflection_visible = False
scene.wfrl_flex_show_tip_trails = False
for obj in scene.objects:
    if any(key in obj.name for key in ('.ClearanceRadar.Beam', '.TipTrail', '.Deflection', '.Reference')):
        obj.hide_render = True
        obj.hide_set(True)
scene.render.engine = 'BLENDER_WORKBENCH'
shading = scene.display.shading
shading.light = 'STUDIO'
shading.studio_light = 'studio.sl'
shading.color_type = 'MATERIAL'
shading.show_shadows = False
shading.show_cavity = True
shading.show_specular_highlight = True
scene.view_settings.view_transform = 'Standard'
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
scene.render.use_border = scene.render.use_crop_to_border = False
scene.render.image_settings.file_format = 'PNG'
bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='DOWN')
camera = scene.camera
root = camera.parent
assert root.name == 'WFRL.Turbine.T1.YawRoot'
mount = camera.location.copy()
shell = scene.objects['WFRL.Turbine.T1.Nacelle']
anchor = Vector(camera['wfrl_mount_anchor'])
offset = Vector(camera['wfrl_mount_offset'])
assert (offset - Vector((0, 0, -.40))).length < 1e-6
assert (mount - anchor - offset).length < 1e-6
assert abs(mount.x + 3.8) < 1e-5 and abs(mount.y) < 1e-5
local_forward = camera.rotation_euler.to_matrix() @ Vector((0, 0, -1))
assert (local_forward - Vector((0, 0, -1))).length < 1e-6
blade = scene.objects['WFRL.Turbine.T1.Blade1']
rest = next(row[1] for row in active.blades if row[0] == blade)
shoulder_ring = np.flatnonzero(np.isclose(rest[:, 2], 8.3333, atol=1e-4))
assert len(shoulder_ring) == 48
shoulder_vertex = int(shoulder_ring[0])
tip_ids = np.flatnonzero(np.isclose(rest[:, 2], rest[:, 2].max(), atol=1e-4))
angles = np.degrees(np.unwrap(np.radians(active.poses[:, 0, 1])))
bottom = 180 + 360 * math.ceil((angles[0] - 180) / 360)


def set_pose(delta):
    at = float(np.interp(bottom + delta, angles, active.times))
    mapped = scene.frame_start + (at - active.times[0]) * 60
    scene.frame_set(math.floor(mapped), subframe=mapped % 1)
    active.update(scene, sim_time_s=at, record_telemetry=False, update_comparison=False)
    bpy.context.view_layer.update()
    assert (camera.location - mount).length < 1e-6
    return at


def uv(point):
    p = world_to_camera_view(scene, camera, point)
    return [float(p.x), float(1 - p.y), float(p.z)]


rows = []
for delta in (-20., -15., -10.):
    at = set_pose(delta)
    graph = bpy.context.evaluated_depsgraph_get()
    point = blade.matrix_world @ blade.data.vertices[shoulder_vertex].co
    tip = sum((blade.matrix_world @ blade.data.vertices[int(i)].co for i in tip_ids), Vector()) / len(tip_ids)
    tip_local = root.matrix_world.inverted() @ tip
    blade.data.calc_loop_triangles()
    shoulder_region = set(int(shoulder_ring[i]) for i in (*range(7), *range(42,48)))
    triangles = [triangle for triangle in blade.data.loop_triangles if shoulder_region.intersection(triangle.vertices)]
    hits = []
    for triangle in triangles:
        indices = tuple(triangle.vertices)
        coords = [blade.matrix_world @ blade.data.vertices[i].co for i in indices]
        sample = sum(coords, Vector()) / 3
        direction = sample - camera.matrix_world.translation
        hit, where, normal, _, obj, _ = scene.ray_cast(graph, camera.matrix_world.translation,
                                                       direction.normalized(), distance=direction.length + .03)
        visible = bool(hit and obj.original == blade and (where - sample).length < .01)
        hits.append(dict(face=list(indices), visible=visible, blocker=obj.name if hit else None,
                         point=uv(sample)))
    direction = point - camera.matrix_world.translation
    exact_hit, exact_where, _, _, exact_object, _ = scene.ray_cast(graph, camera.matrix_world.translation, direction.normalized(), distance=direction.length+.03)
    exact_gap = (exact_where-point).length if exact_hit else None
    exact_visible = bool(exact_hit and exact_object.original == blade and exact_gap < .01)
    visible = [hit for hit in hits if hit['visible'] and 0 < hit['point'][0] < 1 and 0 < hit['point'][1] < 1]
    assert visible, (delta, hits)
    projected = uv(point)
    assert .03 < projected[0] < .97 and .03 < projected[1] < .97, projected
    assert abs(tip_local.y) > 6, 'The frontal blade tip must be laterally clear of the tower axis'
    path = OUT / ('reference.png' if delta == -15 else f'motion-{abs(int(delta))}.png')
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    rows.append(dict(delta_from_down_deg=delta, time_s=at, frame=scene.frame_current,
                     subframe=scene.frame_subframe, shoulder_uv_top_left=projected[:2],
                     shoulder_visible_face_samples=len(visible), shoulder_samples=hits,
                     exact_trailing_edge_visible=exact_visible, exact_trailing_edge_first_hit_gap_m=exact_gap,
                     exact_trailing_edge_blocker=exact_object.name if exact_hit else None,
                     tip_uv_top_left=uv(tip)[:2], tip_lateral_yawroot_m=float(tip_local.y),
                     image=path.name))
set_pose(-15)
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
            space.camera = camera
            space.use_local_camera = False
            space.region_3d.view_perspective = 'CAMERA'
            cameras.fill_camera_view(area, scene)
bpy.ops.object.select_all(action='DESELECT')
scene_path = OUT / 'Rootkink_Down.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
result = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
              build_info=json.loads((PACKAGE / 'build-info.json').read_text()),
              model='rootkink', geometry_sha256=hashlib.sha256((PACKAGE / 'assets/rootkink_geometry.json').read_bytes()).hexdigest(),
              camera=camera.name, parent=root.name, local_location=list(mount),
              shell_anchor=list(anchor), mount_offset=list(offset), mount_length_m=offset.length,
              local_optical_axis=list(local_forward),
              world_nadir_error_deg=math.degrees((camera.matrix_world.to_quaternion() @ Vector((0,0,-1))).angle(Vector((0,0,-1)))),
              optical=dict(yaw=camera['gimbal_yaw'], pitch=camera['gimbal_pitch'], roll=camera['gimbal_roll'],
                           hfov=camera['gimbal_fov'], shift=[camera.data.shift_x,camera.data.shift_y]),
              resolution=[1920,1080], rows=rows, scene=scene_path.name,
              physics_rerun=False, geometry_modified=False, native_window='NOT_RUN',
              scope='Installed payload; three saved-motion poses; finite samples on the outer 15 percent chord near the maximum-chord shoulder. The exact trailing-edge midpoint can be self-occluded. Not complete motion or physical installation acceptance.')
(OUT / 'reference-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')

# Installation markers illustrate the exact shell anchor and optical centre.
orange = bpy.data.materials.new('Mount inspection marker')
orange.diffuse_color = (1,.18,.015,1)
eye = root.matrix_world @ mount
surface = root.matrix_world @ anchor
bpy.ops.mesh.primitive_cube_add(size=.26, location=eye)
marker = bpy.context.object
marker.name = 'Inspection.CameraPosition'
marker.data.materials.append(orange)
segment = eye - surface
bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=.025, depth=segment.length, location=(eye+surface)/2)
connector = bpy.context.object
connector.name = 'Inspection.AnchorToOpticalCentre'
connector.rotation_euler = segment.to_track_quat('Z','Y').to_euler()
connector.data.materials.append(orange)
bpy.ops.mesh.primitive_uv_sphere_add(segments=16,ring_count=8,radius=.09,location=surface)
bpy.context.object.data.materials.append(orange)
review = bpy.data.objects.new('Inspection.MountCamera',bpy.data.cameras.new('Inspection.MountCamera'))
scene.collection.objects.link(review)
review.location = root.matrix_world @ Vector((-9,-13,-4))
target = root.matrix_world @ Vector((-3.6,0,.5))
review.rotation_euler = (target-review.location).to_track_quat('-Z','Y').to_euler()
review.data.type='ORTHO'
review.data.ortho_scale=7.5
scene.camera=review
scene.render.filepath=str(OUT/'camera-mount.png')
bpy.ops.render.render(write_still=True)
print('DOWN_REFERENCE_PASS',json.dumps(result,ensure_ascii=False),flush=True)
