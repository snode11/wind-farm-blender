"""Installed-extension window checks using an existing saved reconstruction.

Open the copied split .blend with this script. WFRL_TEST_OUTPUT is an additive
directory; WFRL_SPLIT_SAVE optionally names a new review file to save on success.
WFRL_ADDON_MODULE selects the installed extension module (default user_default).
WFRL_EXPECTED_ADDON_ROOT optionally requires its exact resolved package directory.
No source import, solver, rendering, or source-result regeneration.
"""
import importlib
import json
import os
from pathlib import Path
import time
import traceback

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Quaternion

MODULE = os.environ.get('WFRL_ADDON_MODULE', 'bl_ext.user_default.wfrl_blender')
EXPECTED_ADDON_ROOT = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
addon = importlib.import_module(MODULE)
split = importlib.import_module(MODULE + '.split_reconstruction')
surface = importlib.import_module(MODULE + '.split_surface_texture')
inspection = importlib.import_module(MODULE + '.blade_recon_inspection')
cameras = importlib.import_module(MODULE + '.cameras')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
state = {'stage': 0, 'deadline': time.monotonic() + 180, 'checks': []}


def views():
    return sorted((a for a in bpy.context.window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.x)


def check_pair():
    scene = bpy.context.scene
    areas = views()
    assert len(areas) == 2, len(areas)
    left, right = (a.spaces.active for a in areas)
    assert right.camera == surface.camera(scene) and right.use_local_camera
    assert right.shading.type == 'MATERIAL'
    assert right.local_view is not None and left.local_view is None
    assert left.show_region_ui and left.show_region_toolbar
    assert left.shading.type == 'MATERIAL' and left.shading.use_scene_world
    for bid in range(1, 4):
        assert surface.objects(scene)[bid-1].local_view_get(right)
        assert not scene.objects[f'SplitRecon.B{bid}'].local_view_get(right)
        assert not scene.objects[f'WFRL.Turbine.T1.Blade{bid}'].local_view_get(right)
    return areas


def check_framing():
    scene = bpy.context.scene
    area = check_pair()[1]
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    space.region_3d.update()
    preview = importlib.import_module(MODULE + '.custom_camera_preview')
    x, y, width, height = preview.available_rectangle(area, region)
    original = scene[surface.INDEX]
    bounds = []
    value = surface.ensure(scene)
    if scene.get(surface.DETAIL):
        blade, radius, tau, size = inspection._coordinates(scene, value['sequence'].rotor)
        target, normal, _up = inspection._surface_sample(value['sequence'], value['blades'][blade],
                                                       blade, original, radius, tau)
        point = location_3d_to_region_2d(region, space.region_3d, target)
        assert point is not None and 8 < point.x < region.width-8 and 8 < point.y < region.height-8
        assert space.region_3d.view_perspective == 'ORTHO'
        assert abs((space.region_3d.view_location - target).length) < .001
        return {'scope': 'local_surface_detail', 'sample_index': original, 'target': list(target),
                'normal': list(normal), 'target_pixels': list(point), 'size_m': size}
    try:
        for index in (0, 16, 30, 60, 90, 119):
            scene[surface.INDEX] = index
            surface.apply(scene)
            bpy.context.view_layer.update()
            depsgraph = bpy.context.evaluated_depsgraph_get()
            points = []
            for bid in range(1, 4):
                ob = surface.objects(scene)[bid-1].evaluated_get(depsgraph)
                for vertex in ob.data.vertices:
                    p = location_3d_to_region_2d(region, space.region_3d, ob.matrix_world @ vertex.co)
                    assert p is not None
                    points.append(p)
            box = [min(p.x for p in points), max(p.x for p in points),
                   min(p.y for p in points), max(p.y for p in points)]
            assert box[0] >= x + 8 and box[1] <= x + width - 8, (index, box, [x, width])
            assert box[2] >= y + 8 and box[3] <= y + height - 8, (index, box, [y, height])
            bounds.append({'sample_index': index, 'pixels': box})
    finally:
        scene[surface.INDEX] = original
        surface.apply(scene)
    return {'region': [region.width, region.height], 'bounds': bounds}


def check_source_framing():
    scene = bpy.context.scene
    area = check_pair()[0]
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    space.region_3d.update()
    x, y, width, height = split._rectangle(area)
    original = scene.frame_current
    bounds = []
    from mathutils import Vector
    try:
        for frame in (1, 52, 901, 1801, 2701, 3601):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            graph = bpy.context.evaluated_depsgraph_get()
            points = []
            for source in scene.objects:
                if (source.type != 'MESH' or not source.name.startswith('WFRL.Turbine.T1.')
                        or not source.visible_get(viewport=space)):
                    continue
                obj = source.evaluated_get(graph)
                for corner in obj.bound_box:
                    point = location_3d_to_region_2d(region, space.region_3d,
                                                    obj.matrix_world @ Vector(corner))
                    assert point is not None, ('source behind view', frame, source.name)
                    points.append(point)
            assert points
            box = [min(p.x for p in points), max(p.x for p in points),
                   min(p.y for p in points), max(p.y for p in points)]
            assert box[0] >= x + 8 and box[1] <= x + width - 8, ('source x', frame, box, [x, width])
            assert box[2] >= y + 8 and box[3] <= y + height - 8, ('source y', frame, box, [y, height])
            bounds.append({'frame': frame, 'pixels': box})
    finally:
        scene.frame_set(original)
    return {'region': [region.width, region.height], 'bounds': bounds}


@bpy.app.handlers.persistent
def tick():
    try:
        assert time.monotonic() < state['deadline'], ('timeout', state)
        scene = bpy.context.scene
        stage = state['stage']
        if cameras._LAYOUT_JOB is not None or scene.get('split_review_status') == 'PREPARING':
            return .2
        if stage == 0:
            addon_path = Path(addon.__file__).resolve()
            module_parts = MODULE.split('.')
            assert len(module_parts) == 3 and module_parts[0] == 'bl_ext', MODULE
            if EXPECTED_ADDON_ROOT:
                expected_root = Path(EXPECTED_ADDON_ROOT).expanduser().resolve()
                assert addon_path.parent == expected_root, (str(addon_path.parent), str(expected_root))
            else:
                expected_fragment = f'/extensions/{module_parts[1]}/{module_parts[2]}/'
                assert expected_fragment in addon_path.as_posix(), (
                    str(addon_path), 'Set WFRL_EXPECTED_ADDON_ROOT for a nonstandard installed repository path')
            assert scene.get('split_reconstruction_review')
            state['initial_rotation'] = list(views()[0].spaces.active.region_3d.view_rotation)
            scene.frame_set(52)
            split.enter(bpy.context, reset_layout=True, use_texture=True)
        elif stage == 1:
            if scene.get('split_review_status') != 'READY':
                return .2
            areas = check_pair()
            state['initial_framing'] = check_framing()
            state['initial_source_framing'] = check_source_framing()
            assert .35 < areas[1].width / (areas[0].width + areas[1].width) < .65
            state['checks'].append('balanced panes, directly visible synthetic texture detail, isolated geometry')
            right = areas[1].spaces.active
            camera = right.camera
            with bpy.context.temp_override(area=areas[0]):
                for angle in ('DOWN', 'NACELLE', 'FRONT'):
                    bpy.ops.wfrl.farm_flex_view(turbine='T1', angle=angle)
                    assert right.camera == camera and right.use_local_camera, angle
                bpy.ops.wfrl.deflection_view()
                assert right.camera == camera and right.use_local_camera
                bpy.ops.wfrl.select_camera(camera_name='WFRL.Camera.T1.FrontQuarter')
                assert right.camera == camera and right.use_local_camera
            state['checks'].append('four camera paths preserve right camera immediately without a repair timer')
            for start, direction, expected in ((16, 1, 17), (16, -1, 15), (0, -1, 0),
                                               (119, 1, 119), (17, -1, 16)):
                scene[surface.INDEX] = start
                surface.apply(scene)
                bpy.ops.wfrl.split_recon_step(direction=direction)
                assert scene[surface.INDEX] == expected, (start, direction, scene[surface.INDEX])
                assert scene.frame_current == 52
            scene.frame_set(52)
            state['checks'].append('independent right sample stepping and endpoint clamp preserve left frame')
            # Region resize: toggling a right sidebar changes usable canvas.
            # Actual separator dragging is checked separately through native UI.
            areas[1].spaces.active.show_region_ui = True
        elif stage == 2:
            state['resized_framing'] = check_framing()
            state['checks'].append('sidebar region resize preserves local detail and independent view')
            views()[1].spaces.active.show_region_ui = False
            state['workspace'] = bpy.context.window.workspace
            other = next((w for w in bpy.data.workspaces if w != state['workspace']), None)
            assert other is not None
            bpy.context.window.workspace = other
        elif stage == 3:
            bpy.context.window.workspace = state['workspace']
        elif stage == 4:
            check_pair()
            assert scene.frame_current == 52
            state['checks'].append('workspace round trip retains independent view roles and time')
            split.exit_mode(bpy.context)
        elif stage == 5:
            assert not split.enabled(scene) and len(views()) == 1
            assert scene.frame_current == 52
            split.enter(bpy.context, reset_layout=True)
        elif stage == 6:
            check_pair()
            assert scene.frame_current == 52
            state['checks'].append('exit and reenter retain time and recreate isolated split')
            with bpy.context.temp_override(area=views()[0]):
                bpy.ops.screen.animation_play()
            state['play_start'] = scene.frame_current
        elif stage == 7:
            assert bpy.context.screen.is_animation_playing
            assert scene.frame_current > state['play_start']
            sample = scene[surface.INDEX]
            bpy.ops.wfrl.split_recon_step(direction=1)
            assert not bpy.context.screen.is_animation_playing
            assert scene[surface.INDEX] == min(sample+1, 119)
            state['checks'].append('left playback advances and independent right sample step pauses playback')
            scene.frame_set(52)
            views()[0].spaces.active.region_3d.view_rotation = Quaternion(state['initial_rotation'])
            split.fit(bpy.context, reset_layout=True)
        elif stage == 8:
            check_pair()
            state['final_framing'] = check_framing()
            state['final_source_framing'] = check_source_framing()
            save = os.environ.get('WFRL_SPLIT_SAVE')
            if save:
                bpy.ops.wm.save_as_mainfile(filepath=save)
            report = {k: v for k, v in state.items() if k not in {'workspace', 'deadline'}}
            report.update(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
                          saved=save, verification='VISIBLE_WINDOW_OPERATORS; NO_FPS_CLAIM')
            (OUT / 'window_validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print('SPLIT_RECON_WINDOW_PASS', flush=True)
            if save and os.environ.get('WFRL_SPLIT_REOPEN') == '1':
                state['stage'] = 9
                return 1.
            return None
        elif stage == 9:
            state['stage'] = 10
            bpy.ops.wm.open_mainfile(filepath=os.environ['WFRL_SPLIT_SAVE'])
            return 3.
        elif stage == 10:
            assert scene.frame_current == 52
            assert scene.get('split_review_status') == 'READY'
            check_pair()
            report = dict(status='PASS', scope='INSTALLED_VISIBLE_SAVE_AND_NATIVE_REOPEN',
                          blend=bpy.data.filepath, addon=addon.__file__, frame=scene.frame_current,
                          right_framing=check_framing(), left_framing=check_source_framing(),
                          external_restore_script_required=False)
            (OUT / 'visible_reopen.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print('FINAL_VISIBLE_NATIVE_REOPEN_PASS', flush=True)
            return None
        state['stage'] += 1
        return 1.
    except Exception:
        (OUT / 'window_validation.json').write_text(json.dumps({
            'status': 'FAIL', 'stage': state['stage'], 'error': traceback.format_exc()}, indent=2))
        traceback.print_exc()
        return None


bpy.app.timers.register(tick, first_interval=2., persistent=True)
