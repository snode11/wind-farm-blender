"""Native save/reopen checks for source views and damaged manual scenes."""
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Euler

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
manual_scene = importlib.import_module(MODULE + '.manual_scene')
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)
RESULT = dict(status='RUNNING', addon_module=MODULE, addon_file=addon.__file__,
              blender=bpy.app.version_string, checks={})


def write_result():
    (OUT / 'checks.json').write_text(json.dumps(RESULT, indent=2), encoding='utf-8')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_snapshot(scene):
    objects = {}
    for obj in scene.objects:
        item = dict(type=obj.type, location=tuple(obj.location), rotation=tuple(obj.rotation_euler),
                    scale=tuple(obj.scale),
                    parent=obj.parent.name if obj.parent else None,
                    hidden=(obj.hide_viewport, obj.hide_render))
        if obj.type == 'MESH':
            item['vertices'] = digest([tuple(vertex.co) for vertex in obj.data.vertices])
            item['materials'] = [dict(name=mat.name, color=tuple(mat.diffuse_color),
                                      roughness=mat.roughness, metallic=mat.metallic)
                                 for mat in obj.data.materials if mat]
        objects[obj.name] = item
    world = scene.world
    background = world.node_tree.nodes.get('Background')
    return dict(scene=scene.name, objects=objects, world=world.name,
                world_color=tuple(world.color), world_nodes=world.use_nodes,
                background_color=tuple(background.inputs['Color'].default_value),
                background_strength=background.inputs['Strength'].default_value,
                camera=scene.camera.name if scene.camera else None,
                frame=scene.frame_current)


def view_snapshot():
    result = []
    for index, area in enumerate(bpy.context.screen.areas):
        if area.type != 'VIEW_3D':
            continue
        space, region = area.spaces.active, area.spaces.active.region_3d
        result.append(dict(area_index=index, location=tuple(region.view_location),
                           rotation=tuple(region.view_rotation), distance=region.view_distance,
                           perspective=region.view_perspective,
                           camera_zoom=region.view_camera_zoom,
                           camera_offset=tuple(region.view_camera_offset),
                           camera=space.camera.name if space.camera else None,
                           local_camera=space.use_local_camera,
                           clip_start=space.clip_start, clip_end=space.clip_end,
                           shading=space.shading.type, color_type=space.shading.color_type,
                           scene_world=space.shading.use_scene_world,
                           scene_lights=space.shading.use_scene_lights,
                           overlays=space.overlay.show_overlays, sidebar=space.show_region_ui))
    assert result, 'Source view restoration requires a VIEW_3D area'
    return result


def set_source_view(source, variant):
    for index, area in enumerate(bpy.context.screen.areas):
        if area.type != 'VIEW_3D':
            continue
        space, region = area.spaces.active, area.spaces.active.region_3d
        region.view_location = (13.25 + variant + index, -27.5 - variant, 51.75)
        region.view_rotation = Euler((.3 + variant * .1, -.5, .7 + index * .1), 'XYZ').to_quaternion()
        region.view_distance = 231.5 + variant * 11 + index
        region.view_perspective = 'CAMERA'
        region.view_camera_zoom = 28.75 + variant + index
        region.view_camera_offset = (.17 + variant * .01, -.23 - index * .01)
        space.camera = source.camera
        space.use_local_camera = True
        space.clip_start, space.clip_end = .25, 4321.0 + variant
        space.shading.type, space.shading.color_type = 'WIREFRAME', 'OBJECT'
        space.shading.use_scene_world = False
        space.shading.use_scene_lights = False
        space.overlay.show_overlays = True
        space.show_region_ui = False
    return view_snapshot()


def views_equal(expected, actual):
    if len(expected) != len(actual):
        return False
    numeric = {'location', 'rotation', 'distance', 'camera_zoom', 'camera_offset',
               'clip_start', 'clip_end'}
    for before, after in zip(expected, actual):
        for key in before:
            if key in numeric:
                a = before[key] if isinstance(before[key], (list, tuple)) else (before[key],)
                b = after[key] if isinstance(after[key], (list, tuple)) else (after[key],)
                if len(a) != len(b) or any(not math.isclose(x, y, abs_tol=1e-5, rel_tol=1e-6)
                                          for x, y in zip(a, b)):
                    return False
            elif before[key] != after[key]:
                return False
    return True


def assert_isolated(source, manual):
    assert set(source.objects).isdisjoint(set(manual.objects)), 'Manual scene shares source objects'
    assert source.world != manual.world, 'Manual scene shares the source World'
    source_meshes = {obj.data for obj in source.objects if obj.type == 'MESH'}
    manual_meshes = {obj.data for obj in manual.objects if obj.type == 'MESH'}
    assert source_meshes.isdisjoint(manual_meshes), 'Manual scene shares source meshes'
    source_materials = {mat for mesh in source_meshes for mat in mesh.materials if mat}
    manual_materials = {mat for mesh in manual_meshes for mat in mesh.materials if mat}
    assert source_materials.isdisjoint(manual_materials), 'Manual scene shares source materials'


def round_trip(source, source_before, case, *, damage=False, reopen_damaged=False,
               reopen=True):
    source_name = source.name
    expected_view = set_source_view(source, 7 if damage else 3)
    prior_manual_name = source.wfrl_manual_scene.name if source.wfrl_manual_scene else None
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    manual = bpy.context.scene
    assert control.validate_scene(manual)[0], control.validate_scene(manual)
    if not damage:
        assert manual.name == prior_manual_name, 'Existing manual scene was unnecessarily replaced'
    assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
    down_views = view_snapshot()
    assert all(view['perspective'] == 'CAMERA' for view in down_views)
    assert any(before['camera_zoom'] != after['camera_zoom']
               or before['camera_offset'] != after['camera_offset']
               for before, after in zip(expected_view, down_views)), 'Down did not exercise camera framing'
    assert not views_equal(expected_view, view_snapshot()), 'Manual focus must differ from the source view'
    original_return_data = manual.get('wfrl_manual_return_views')
    assert original_return_data, 'The regular enter path did not persist its source view'
    case_result = RESULT['checks'][case] = dict(expected_view=expected_view,
                                             original_saved_return_view_present=True)
    if damage:
        broken_name = manual.name
        victim = manual.wfrl_manual_turbines[0].blade1_object
        bpy.data.objects.remove(victim, do_unlink=True)
        assert not control.validate_scene(manual)[0], 'Deleted blade was accepted'
        if reopen_damaged:
            damaged_path = OUT / (case + '-damaged.blend')
            bpy.ops.wm.save_as_mainfile(filepath=str(damaged_path))
            bpy.ops.wm.open_mainfile(filepath=str(damaged_path))
            source, manual = bpy.data.scenes[source_name], bpy.data.scenes[broken_name]
            assert bpy.context.scene == manual
            assert not manual_scene._VIEWS, 'Reopened damage must rely on the stored source view'
            assert not control.validate_scene(manual)[0]
            case_result['reopened_damaged_scene'] = True
        assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
        manual = bpy.context.scene
        assert manual.name != broken_name, 'Recovery did not construct a new scene'
        assert bpy.data.scenes.get(broken_name), 'Recovery removed the damaged user scene'
        assert control.validate_scene(manual)[0], control.validate_scene(manual)
        assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
        case_result['recovery_preserves_damaged_scene'] = True
    assert_isolated(source, manual)
    assert source_snapshot(source) == source_before, 'Manual construction changed source data'
    case_result['objects_meshes_materials_world_isolated'] = True
    case_result['saved_return_view_present'] = bool(manual.get('wfrl_manual_return_views'))
    case_result['saved_return_view_retained'] = json.loads(manual.get('wfrl_manual_return_views', '[]')) == json.loads(original_return_data)
    manual_name = manual.name
    blend_path = OUT / (case + '.blend') if reopen else None
    if reopen:
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        source, manual = bpy.data.scenes[source_name], bpy.data.scenes[manual_name]
        assert bpy.context.scene == manual
        assert not manual_scene._VIEWS, 'Save/reopen must restore from persisted data, not memory'
        assert control.validate_scene(manual)[0], control.validate_scene(manual)
        assert_isolated(source, manual)
        reopened_source = source_snapshot(source)
        if reopened_source != source_before:
            (OUT / (case + '-source-difference.json')).write_text(
                json.dumps(dict(expected=source_before, actual=reopened_source), indent=2), encoding='utf-8')
        assert reopened_source == source_before, 'Save/reopen changed the original scene'
    case_result['before_leave'] = view_snapshot()
    assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
    assert bpy.context.scene == source
    case_result['after_leave'] = view_snapshot()
    case_result['restored_source_view'] = views_equal(expected_view, case_result['after_leave'])
    case_result['source_data_preserved'] = source_snapshot(source) == source_before
    case_result['blend_path'] = str(blend_path) if blend_path else None
    write_result()
    assert case_result['restored_source_view'], f'{case}: original source view was not restored'
    assert case_result['source_data_preserved'], f'{case}: source data changed'
    assert case_result['saved_return_view_retained'], f'{case}: original return snapshot was replaced'
    return source


def run():
    addon.register()
    source = bpy.context.scene
    source.name = 'Manual Recovery Source'
    source.use_fake_user = True
    source.frame_set(37)
    material = bpy.data.materials.new('Manual Recovery Source Material')
    material.diffuse_color = (.12, .31, .63, 1)
    material.roughness, material.metallic = .37, .23
    source.objects['Cube'].data.materials.clear()
    source.objects['Cube'].data.materials.append(material)
    source.world = bpy.data.worlds.new('Manual Recovery Source World')
    source.world.use_nodes = True
    background = source.world.node_tree.nodes.get('Background')
    background.inputs['Color'].default_value = (.17, .23, .41, 1)
    background.inputs['Strength'].default_value = .37
    bpy.context.view_layer.update()
    before = source_snapshot(source)
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
    source = round_trip(source, before, 'existing_scene_direct_return', reopen=False)
    source = round_trip(source, before, 'existing_scene_save_reopen')
    source = round_trip(source, before, 'damaged_scene_recreate_save_reopen', damage=True)
    round_trip(source, before, 'reopened_damaged_scene_recreate_save_reopen',
               damage=True, reopen_damaged=True)
    addon.unregister()
    RESULT['status'] = 'PASS'
    write_result()
    print('MANUAL_SCENE_RECOVERY_PASS ' + json.dumps(RESULT), flush=True)


try:
    run()
except Exception as exc:
    RESULT['status'] = 'FAIL'
    RESULT['error'] = str(exc)
    RESULT['traceback'] = traceback.format_exc()
    write_result()
    raise
