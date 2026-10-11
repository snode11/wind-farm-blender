"""Visible-window operator, animation, framing and native reopen acceptance.

Launch an independent Blender window with a COPY of the bundled geometry scene
before --python this_file. Use an isolated BLENDER_USER_CONFIG/profile and a new
WFRL_TEST_OUTPUT directory. WFRL_ADDON_MODULE/WFRL_EXPECTED_ADDON_ROOT identify
the source or installed package; installed evidence requires a bl_ext module.
WFRL_SPLIT_SAVE is an optional new .blend inside that output directory.

The script saves JSON and actual screen.screenshot PNGs, runs real native
animation, and leaves the tested window visible, paused at frame 52, for human
inspection. It never closes another window or saves over the canonical scene.
WFRL_QUIT_AFTER_TEST=1 closes only this dedicated process after success/failure.
WFRL_HOLD_SECONDS optionally holds the initial texture screenshot; a file named
continue-same-source inside output releases that hold early.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import time
import traceback
from types import SimpleNamespace

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('same_source_validation_helpers',
                                             HERE / 'mappo_same_source_texture_regression.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
OUT.mkdir(parents=True, exist_ok=True)
state = dict(stage=0, checks=[], deadline=time.monotonic() + 420)
validation = None


def views():
    return sorted((area for area in bpy.context.window.screen.areas if area.type == 'VIEW_3D'),
                  key=lambda area: area.x)


def operator(name, **kwargs):
    area = views()[0]
    region = next(region for region in area.regions if region.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        result = getattr(bpy.ops.wfrl, name)(**kwargs)
    assert result == {'FINISHED'}, (name, result)


def pair(textured=True):
    scene = validation.scene
    areas = views()
    assert len(areas) == 2
    left, right = [area.spaces.active for area in areas]
    assert validation.split.enabled(scene) and scene.get('split_review_status') == 'READY'
    assert right.local_view is not None and left.local_view is None
    assert right.use_local_camera and left.show_region_ui and left.show_region_toolbar
    camera = validation.same.camera(scene) if textured else scene.objects['SplitRecon.Camera']
    assert right.camera == camera
    assert left.shading.type == 'MATERIAL'
    assert right.shading.type == ('MATERIAL' if textured else 'SOLID')
    assert validation.same.active(scene) is textured
    assert not validation.synthetic.active(scene)
    for bid in (1, 2, 3):
        source = scene.objects[f'WFRL.Turbine.T1.Blade{bid}']
        original = scene.objects[f'SplitRecon.B{bid}']
        assert not source.local_view_get(right)
        assert original.local_view_get(right) is not textured
        if textured:
            assert validation.same.objects(scene)[bid - 1].local_view_get(right)
    return areas


class RecordedLayout:
    def __init__(self):
        self.labels, self.operators = [], []

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: self

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))

    def operator(self, name, **kwargs):
        entry = SimpleNamespace(name=name, text=kwargs.get('text', ''))
        self.operators.append(entry)
        return entry


def controls():
    ui = helpers.importlib.import_module(validation.module + '.split_reconstruction_ui')
    layout = RecordedLayout()
    ui.draw_controls(layout, bpy.context)
    entries = [vars(entry) for entry in layout.operators]
    restore = [entry for entry in entries if entry['text'] == '恢复布局']
    if validation.split.enabled(validation.scene):
        assert len(restore) == 1 and restore[0].get('mode') == 'CURRENT', restore
    return dict(labels=layout.labels, operators=entries)


def detail_framing():
    scene = validation.scene
    assert scene.get(validation.same.DETAIL)
    area = pair()[1]
    space = area.spaces.active
    region = next(region for region in area.regions if region.type == 'WINDOW')
    space.region_3d.update()
    inspection = helpers.importlib.import_module(validation.module + '.blade_recon_inspection')
    sequence = validation.value['sequence']
    blade, radius, tau, size = inspection._coordinates(scene, sequence.rotor)
    target, normal, _up = inspection._surface_sample(sequence, validation.same.objects(scene)[blade],
                                                    blade, scene[validation.same.INDEX], radius, tau)
    point = location_3d_to_region_2d(region, space.region_3d, target)
    assert point is not None and 8 < point.x < region.width - 8 and 8 < point.y < region.height - 8
    assert space.region_3d.view_perspective == 'ORTHO'
    assert (space.region_3d.view_location - target).length < .001
    return dict(index=scene[validation.same.INDEX], target_m=list(target), normal=list(normal),
                target_pixels=list(point), size_m=size, region_size=[region.width, region.height])


def full_framing():
    scene = validation.scene
    area = pair()[1]
    space = area.spaces.active
    region = next(region for region in area.regions if region.type == 'WINDOW')
    space.region_3d.update()
    preview = helpers.importlib.import_module(validation.module + '.custom_camera_preview')
    x, y, width, height = preview.available_rectangle(area, region)
    original = scene.frame_current
    checks = []
    try:
        for frame in (1, 52, 901, 1801, 2701, 3601):
            validation.frame(frame)
            points = []
            graph = bpy.context.evaluated_depsgraph_get()
            for source in validation.same.objects(scene):
                obj = source.evaluated_get(graph)
                for vertex in obj.data.vertices:
                    point = location_3d_to_region_2d(region, space.region_3d, obj.matrix_world @ vertex.co)
                    assert point is not None
                    points.append(point)
            box = [min(p.x for p in points), max(p.x for p in points),
                   min(p.y for p in points), max(p.y for p in points)]
            assert box[0] >= x + 8 and box[1] <= x + width - 8, (frame, box, [x, width])
            assert box[2] >= y + 8 and box[3] <= y + height - 8, (frame, box, [y, height])
            checks.append(dict(frame=frame, pixels=box))
    finally:
        validation.frame(original)
    return dict(region_size=[region.width, region.height], samples=checks)


def right_view():
    space = pair()[1].spaces.active
    return dict(camera=space.camera.name, location=list(space.region_3d.view_location),
                rotation=list(space.region_3d.view_rotation), distance=space.region_3d.view_distance,
                perspective=space.region_3d.view_perspective, lens=space.lens,
                lock_object=space.lock_object.name if space.lock_object else None,
                lock_cursor=space.lock_cursor, lock_camera=space.lock_camera,
                show_region_ui=space.show_region_ui, show_region_toolbar=space.show_region_toolbar)


def capture(name, *, detail=True):
    data = dict(frame=validation.scene.frame_current, controls=controls(), right_view=right_view())
    if detail:
        data['detail_framing'] = detail_framing()
    data['screenshot'] = str(OUT / (name + '.png'))
    bpy.ops.screen.screenshot(filepath=data['screenshot'])
    (OUT / (name + '-ready.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    state[name] = data
    print('MAPPO_SAME_SOURCE_WINDOW_READY', name, flush=True)


def report(status, error=None):
    data = {key: value for key, value in state.items() if key not in {'deadline', 'hold_until'}}
    data.update(status=status, addon_module=validation.module if validation else None,
                addon_file=validation.addon.__file__ if validation else None,
                actual_module_root=str(validation.root) if validation else None,
                blender=bpy.app.version_string, blend=bpy.data.filepath, error=error,
                verification='VISIBLE_WINDOW_NATIVE_ANIMATION_OPERATORS_SCREENSHOTS_AND_REOPEN',
                software_sync_acceptance_only=True, reconstruction_accuracy_accepted=False,
                visible_window_left_open=os.environ.get('WFRL_QUIT_AFTER_TEST') != '1')
    (OUT / 'window-validation.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def tick():
    global validation
    try:
        assert not bpy.app.background and bpy.context.window is not None
        assert time.monotonic() < state['deadline'], ('timeout', state['stage'])
        if validation is not None:
            scene = bpy.context.scene
            assert not str(scene.get('split_review_status', '')).startswith('ERROR'), scene.get('split_review_status')
            cameras = helpers.importlib.import_module(validation.module + '.cameras')
            if (state['stage'] != 16 and (cameras._LAYOUT_JOB is not None or validation.split._JOBS
                    or scene.get('split_review_status') == 'PREPARING')):
                return .2
        stage = state['stage']
        if stage == 0:
            validation = helpers.Validation(restore_farm=os.environ.get('WFRL_ADDON_MODULE') == 'wfrl_blender')
            scene = validation.scene
            canonical = validation.root / 'assets/examples/mappo_reconstruction_split.blend'
            assert helpers.sha256(canonical) == os.environ.get('WFRL_EXPECTED_GEOMETRY_SHA256', helpers.GEOMETRY_SHA256)
            assert Path(bpy.data.filepath).resolve() != canonical.resolve(), 'Use a copied scene in this dedicated process'
            assert not validation.same.active(scene) and not validation.synthetic.active(scene)
            state['original_geometry_identity'] = validation.geometry_identity()
            state['original_operator_label'] = bpy.ops.wfrl.split_recon_open_example.get_rna_type().name
            state['same_source_operator_label'] = bpy.ops.wfrl.split_recon_same_source.get_rna_type().name
            scene.frame_set(52)
            validation.split.enter(bpy.context, reset_layout=True)
        elif stage == 1:
            pair(False)
            assert validation.scene.frame_current == 52
            state['geometry_controls'] = controls()
            bpy.ops.screen.screenshot(filepath=str(OUT / 'original-geometry.png'))
            operator('split_recon_same_source')
        elif stage == 2:
            pair()
            validation.activate()
            state['provenance'] = validation.provenance()
            state['saved_geometry'] = validation.all_saved_geometry()
            state['immutable_texture'] = validation.immutable_texture()
            validation.frame(52, seek=False)
            assert validation.geometry_identity() == state['original_geometry_identity']
            assert not scene.get(validation.same.DETAIL)
            state['default_full_rotor_framing'] = full_framing()
            state['checks'].append('The explicit same-source entry defaults to full rotor framing')
            operator('split_recon_inspect', point='CURRENT')
            capture('same-source-detail')
            state['hold_until'] = time.monotonic() + float(os.environ.get('WFRL_HOLD_SECONDS', '0'))
        elif stage == 3:
            if time.monotonic() < state['hold_until'] and not (OUT / 'continue-same-source').exists():
                return .2
            validation.frame(49)
            state['play_start'] = validation.motion_signature()
            with bpy.context.temp_override(area=views()[0]):
                bpy.ops.screen.animation_play()
        elif stage == 4:
            assert bpy.context.screen.is_animation_playing
            current = validation.motion_signature()
            start = state['play_start']
            assert current['frame'] > start['frame'] and current['index'] > start['index'], (start, current)
            assert current['left'] != start['left'] and current['right'] != start['right'], (start, current)
            state['played_frame'] = validation.frame(current['frame'], seek=False)
            state['play_end'] = current
            with bpy.context.temp_override(area=views()[0]):
                bpy.ops.screen.animation_cancel(restore_frame=False)
            state['pause_start'] = validation.motion_signature()
            capture('paused-after-real-play')
            state['checks'].append('Real visible-window animation moved both left MAPPO and right held saved geometry')
        elif stage == 5:
            assert not bpy.context.screen.is_animation_playing
            assert validation.motion_signature() == state['pause_start']
            validation.frame(52)
            state['checks'].append('Native pause holds both left and right without an extra texture refresh')
            with bpy.context.temp_override(area=views()[0]):
                bpy.ops.screen.animation_play()
        elif stage == 6:
            assert bpy.context.screen.is_animation_playing
            expected = validation.timing.step_sample_frame(validation.scene.frame_current, 1,
                validation.timing.spec_from_scene(validation.scene))
            operator('split_recon_step', direction=1)
            assert not bpy.context.screen.is_animation_playing
            assert validation.scene.frame_current == expected
            validation.frame(expected, seek=False)
            validation.frame(52)
            operator('split_recon_step', direction=1)
            assert validation.scene.frame_current == 55
            validation.frame(55, seek=False)
            detail_framing()
            state['checks'].append('Sample step during native play first pauses both views; frame52 next actual sample is frame55')
            operator('split_recon_step', direction=-1)
            assert validation.scene.frame_current == 49
            validation.frame(49, seek=False)
            for start, direction in ((1, -1), (3601, 1)):
                validation.frame(start)
                operator('split_recon_step', direction=direction)
                assert validation.scene.frame_current == start
                validation.frame(start, seek=False)
            validation.frame(52)
            state['checks'].append('Previous actual sample and first/last endpoint clamps use the same shared clock')
            state['right_before_source_camera'] = right_view()
            operator('farm_flex_view', turbine='T1', angle='DOWN')
        elif stage == 7:
            state['right_after_source_camera_down'] = right_view()
            assert state['right_after_source_camera_down'] == state['right_before_source_camera'], (
                state['right_before_source_camera'], state['right_after_source_camera_down'])
            validation.frame(52, seek=False)
            operator('farm_flex_view', turbine='T1', angle='FRONT')
        elif stage == 8:
            state['right_after_source_camera_front'] = right_view()
            assert state['right_after_source_camera_front'] == state['right_before_source_camera'], (
                state['right_before_source_camera'], state['right_after_source_camera_front'])
            state['checks'].append('Left source camera switches retain right camera, local view and surface framing')
            operator('split_recon_enter', mode='CURRENT')
        elif stage == 9:
            pair()
            validation.frame(52, seek=False)
            assert validation.immutable_texture() == state['immutable_texture']
            state['checks'].append('CURRENT restore preserves MAPPO same-source selection, texture data and time')
            operator('split_recon_fit')
        elif stage == 10:
            pair()
            state['full_rotor_framing'] = full_framing()
            capture('same-source-full-rotor', detail=False)
            operator('split_recon_inspect', point='CURRENT')
        elif stage == 11:
            pair()
            validation.frame(52, seek=False)
            detail_framing()
            operator('split_recon_exit')
        elif stage == 12:
            assert not validation.split.enabled(validation.scene) and len(views()) == 1
            assert validation.scene.frame_current == 52
            assert validation.same.active(validation.scene)
            operator('split_recon_enter', mode='CURRENT')
        elif stage == 13:
            pair()
            validation.frame(52, seek=False)
            detail_framing()
            assert validation.geometry_identity() == state['original_geometry_identity']
            assert validation.immutable_texture() == state['immutable_texture']
            state['checks'].append('Exit and CURRENT reenter preserve selected data and recreate isolated split')
            capture('same-source-current-reentry')
            validation.frame(3598)
            state['endpoint_playback'] = dict(start_frame=3598, start=validation.motion_signature(),
                deadline=time.monotonic() + 8., observed=[], user_operator='wfrl.clearance_playback')
            operator('clearance_playback')
            assert bpy.context.screen.is_animation_playing
            state['stage'] = 16
            return .05
        elif stage == 14:
            state['stage'] = 15
            bpy.ops.wm.open_mainfile(filepath=state['saved'], load_ui=True, use_scripts=False)
            return 3.
        elif stage == 15:
            validation = helpers.Validation()
            assert validation.same.information(validation.scene) is not None, 'Native load lifecycle did not restore texture session'
            validation.activate()
            pair()
            validation.frame(52, seek=False)
            assert validation.geometry_identity() == state['original_geometry_identity']
            assert validation.immutable_texture() == state['immutable_texture']
            capture('same-source-native-reopen')
            state['checks'].append('Additive save and native visible reopen restore both source and texture without an external repair script')
            report('PASS')
            print('MAPPO_SAME_SOURCE_TEXTURE_WINDOW_PASS', flush=True)
            if os.environ.get('WFRL_QUIT_AFTER_TEST') == '1':
                bpy.ops.wm.quit_blender()
            return None
        elif stage == 16:
            endpoint = state['endpoint_playback']
            frame = validation.scene.frame_current
            playing = bpy.context.screen.is_animation_playing
            endpoint['observed'].append(dict(frame=frame, index=validation.scene[validation.same.INDEX],
                                            is_animation_playing=playing))
            if not playing:
                assert frame == 3601, ('Unexpected playback stop before saved endpoint', frame)
                endpoint['behavior'] = 'STOP_AT_LAST_SAVED_SAMPLE'
            elif frame < endpoint['start_frame']:
                endpoint['behavior'] = 'NATIVE_LOOP_BOTH_VIEWS_RETURNED_TO_START_INTERVAL'
                operator('clearance_playback')
                assert not bpy.context.screen.is_animation_playing
            else:
                assert time.monotonic() < endpoint['deadline'], ('Endpoint behavior not reached in bounded playback', endpoint)
                return .05
            endpoint['final_frame_validation'] = validation.frame(frame, seek=False)
            endpoint['final_motion_signature'] = validation.motion_signature()
            if endpoint['behavior'].startswith('NATIVE_LOOP'):
                assert endpoint['final_motion_signature']['left'] != endpoint['start']['left']
                assert endpoint['final_motion_signature']['right'] != endpoint['start']['right']
            endpoint.pop('deadline')
            state['checks'].append('Actual native endpoint playback observed with the user play/pause operator: ' + endpoint['behavior'])
            validation.frame(52)
            state['stage'] = 17
            return .2
        elif stage == 17:
            pair()
            detail_framing()
            state['saved'] = str(helpers.save_path(OUT))
            bpy.ops.wm.save_as_mainfile(filepath=state['saved'])
            state['stage'] = 14
            return 1.
        state['stage'] += 1
        return 1.
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        report('FAIL', error)
        if os.environ.get('WFRL_QUIT_AFTER_TEST') == '1':
            bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=3., persistent=True)
