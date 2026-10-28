"""Installed, visible-window acceptance of the unified reconstruction workflow.

Start a NEW, clean Blender process with this script (no --background). Set
WFRL_ADDON_MODULE=bl_ext.user_default.wfrl_blender, WFRL_EXPECTED_ADDON_ROOT to
the installation, and WFRL_TEST_OUTPUT to a NEW directory. The installed
``打开几何重建示例…`` operator opens its canonical asset using Blender's native
unsaved-work protection. The test refuses to bypass a dirty startup scene.

Only additive .blend copies and screen.screenshot PNGs are written to output.
No solver, capture, render, asset edit, or canonical-file save is performed.
Native view_camera/view_orbit/zoom operations exercise navigation; actual mouse
wheel, sidebar readability, and texture visibility still need human/CUA review.
The persistent timer survives native open_mainfile calls. The tested process
stays visible and paused unless WFRL_QUIT_AFTER_TEST=1 explicitly closes it.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import time
import traceback
from types import SimpleNamespace

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector
import numpy as np


MODULE = os.environ['WFRL_ADDON_MODULE']
EXPECTED_ROOT = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).expanduser().resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
REPORT = OUT / 'reconstruction-workflow-window-validation.json'
OUT.mkdir(parents=True, exist_ok=True)
assert not REPORT.exists(), 'Use a new output directory'
GEOMETRY_SHA256 = '3475e8e43e489076481da2365b3d7179f8667c065ee8dc580486d374c98f616b'
state = dict(stage=0, checks=[], deadline=time.monotonic() + 480.,
             smooth_view_preference=bpy.context.preferences.view.smooth_view)
modules = None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_modules():
    global modules
    assert MODULE.startswith('bl_ext.'), 'Installed acceptance requires a bl_ext module'
    addon = importlib.import_module(MODULE)
    root = Path(addon.__file__).resolve().parent
    assert root == EXPECTED_ROOT, (root, EXPECTED_ROOT)
    if not hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'):
        bpy.ops.preferences.addon_enable(module=MODULE)
    modules = SimpleNamespace(addon=addon, root=root, **{
        name: importlib.import_module(MODULE + '.' + filename)
        for name, filename in dict(core='split_reconstruction', same='split_mappo_texture',
            synth='split_surface_texture', timing='split_reconstruction_timing',
            farm='farm_flex', clearance='clearance_replay', cameras='cameras',
            ui='split_reconstruction_ui', preview='custom_camera_preview',
            panel='panels.farm_replay').items()})
    return modules


def views():
    return sorted((a for a in bpy.context.window.screen.areas if a.type == 'VIEW_3D'),
                  key=lambda a: (a.x, -a.y))


def region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def operator(name, **kwargs):
    area = views()[0]
    with bpy.context.temp_override(window=bpy.context.window, area=area, region=region(area)):
        result = getattr(bpy.ops.wfrl, name)(**kwargs)
    assert result == {'FINISHED'}, (name, kwargs, result)


def native(name, **kwargs):
    area = pair()[1]
    with bpy.context.temp_override(window=bpy.context.window, area=area, region=region(area)):
        result = getattr(bpy.ops.view3d, name)(**kwargs)
    assert result == {'FINISHED'}, (name, kwargs, result)
    return max(.35, bpy.context.preferences.view.smooth_view / 1000. + .2)


def mode():
    return 'MAPPO' if modules.same.active(bpy.context.scene) else 'GEOMETRY'


def right_objects():
    scene = bpy.context.scene
    if mode() == 'MAPPO':
        return modules.same.objects(scene)
    return [scene.objects['SplitRecon.B' + str(i)] for i in (1, 2, 3)] + [
        scene.objects[name] for name in scene['split_reconstruction_structure_names']]


def source_objects():
    scene = bpy.context.scene
    blades = [scene.objects['WFRL.Turbine.T1.Blade' + str(i)] for i in (1, 2, 3)]
    structures = [scene.objects[obj['split_turbine_context_source']]
                  for obj in right_objects()[3:]]
    assert all(scene.objects['WFRL.Turbine.T1.' + name] in structures
               for name in ('Tower', 'Nacelle', 'Hub', 'Spinner'))
    return blades + structures


def pair(expected=None):
    scene = bpy.context.scene
    actual = mode()
    if expected:
        assert actual == expected, (actual, expected)
    areas = views()
    assert len(areas) == 2, len(areas)
    left, right = [a.spaces.active for a in areas]
    assert modules.core.enabled(scene) and scene.get('split_review_status') == 'READY'
    assert not modules.synth.active(scene), 'Independent synth_tex is outside this workflow'
    assert left.local_view is None and right.local_view is not None
    assert right.use_local_camera and right.camera == modules.core.reconstruction_camera(scene)
    assert not right.camera.data.show_passepartout
    assert left.shading.type == 'MATERIAL'
    assert right.shading.type == ('MATERIAL' if actual == 'MAPPO' else 'SOLID')
    objects = right_objects()
    names_key = ('split_mappo_texture_structure_names' if actual == 'MAPPO'
                 else 'split_reconstruction_structure_names')
    structure_names = list(scene[names_key])
    assert len(structure_names) > 4 and len(structure_names) == len(set(structure_names))
    assert len(objects) == 3 + len(structure_names)
    assert [obj.name for obj in objects[3:]] == structure_names
    assert all(o is not None and o.type == 'MESH' for o in objects)
    assert all(o.data.shape_keys and len(o.data.shape_keys.key_blocks) == 601 for o in objects[:3])
    assert all(o.get('split_turbine_context_classification') == 'SIMULATED_MODEL_CONTEXT'
               for o in objects[3:])
    for obj in objects:
        assert obj.local_view_get(right) and obj.visible_get(viewport=right), obj.name
    for obj in source_objects():
        assert not obj.local_view_get(right), ('Source leaked to reconstruction viewport', obj.name)
        assert obj.visible_get(viewport=left), ('Source missing from source viewport', obj.name)
    other = ([scene.objects['SplitRecon.B' + str(i)] for i in (1, 2, 3)] +
             [scene.objects[name] for name in scene['split_reconstruction_structure_names']]
             if actual == 'MAPPO' else modules.same.owned(scene))
    for obj in other:
        assert not obj.local_view_get(right), ('Inactive mode leaked to right viewport', obj.name)
    return areas


def navigation(area):
    space = area.spaces.active
    rv = space.region_3d
    return dict(location=list(rv.view_location), rotation=list(rv.view_rotation),
        distance=rv.view_distance, perspective=rv.view_perspective,
        camera_zoom=rv.view_camera_zoom, camera_offset=list(rv.view_camera_offset),
        lens=space.lens, camera=space.camera.name if space.camera else None,
        lock_object=space.lock_object.name if space.lock_object else None,
        lock_cursor=space.lock_cursor, lock_camera=space.lock_camera)


def navigation_pair():
    return [navigation(area) for area in pair()]


def display_origin():
    scene = bpy.context.scene
    obj = modules.same.blades(scene)[0] if mode() == 'MAPPO' else scene.objects['SplitRecon.B1']
    return list(obj.matrix_world.translation)


def unchanged(expected, *, cameras=True, right_offset=None, mode_transfer=False):
    actual = navigation_pair()
    for index, (original, after) in enumerate(zip(expected, actual)):
        before = dict(original)
        if index == 1 and right_offset is not None:
            before['location'] = list(Vector(original['location']) + Vector(right_offset))
        for key in ('perspective', 'lock_object', 'lock_cursor', 'lock_camera'):
            assert after[key] == before[key], (key, before, after)
        if cameras:
            assert after['camera'] == before['camera'], (before, after)
        for key in ('location', 'rotation', 'distance', 'camera_zoom', 'camera_offset', 'lens'):
            tolerance = 1e-5
            if key == 'location' and index == 1 and (mode_transfer or right_offset is not None):
                # RegionView3D navigation is native float32. Translating to the
                # other display origin and back can lose one ULP at 10 km.
                # This allowance applies ONLY to navigation origin transfer;
                # geometry, timing, rotation, zoom and playback stay unchanged.
                origin_values = list(modules.same.OFFSET) + list(display_origin())
                origin_values += before['location'] + after['location']
                scale = max(abs(float(value)) for value in origin_values)
                ulp = abs(float(np.spacing(np.float32(scale))))
                tolerance = max(tolerance, ulp)
                error = float(np.max(np.abs(np.asarray(after[key]) - np.asarray(before[key]))))
                state.setdefault('navigation_origin_transfer_checks', []).append(dict(
                    stage=state['stage'], error_m=error, tolerance_m=tolerance,
                    native_float32_origin_ulp_m=ulp, scope='NAVIGATION_ONLY_ORIGIN_TRANSLATION',
                    expected=before['location'], actual=after['location']))
            assert np.allclose(after[key], before[key], atol=tolerance, rtol=0), (key, before, after, tolerance)
    return actual


def coordinates(obj):
    obj = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', values)
    return values.reshape(-1, 3)


def signature():
    scene = bpy.context.scene
    result = dict(frame=scene.frame_current, mode=mode(), left=[], right=[])
    right = (modules.same.blades(scene) if mode() == 'MAPPO' else
             [scene.objects['SplitRecon.B' + str(i)] for i in (1, 2, 3)])
    for side, objects in (('left', source_objects()[:3]), ('right', right)):
        for obj in objects:
            values = coordinates(obj)
            matrix = np.asarray(obj.matrix_world, dtype=np.float64)
            world = values @ matrix[:3, :3].T + matrix[:3, 3]
            result[side].append(hashlib.sha256(world.tobytes()).hexdigest())
    result['index'] = clock()['index']
    return result


def clock():
    scene = bpy.context.scene
    spec = modules.timing.spec_from_scene(scene)
    value = modules.timing.timing_for_frame(scene.frame_current, spec)
    assert spec.samples == 601 and spec.sampling_hz == 10 and spec.timeline_fps == 60
    sample = modules.clearance.sample(scene)
    assert sample and abs(sample['time_s'] - value.timeline_time_s) < 1e-8, sample
    assert value.in_range
    assert value.sample_frame <= scene.frame_current < value.sample_frame + spec.stride
    record = dict(frame=scene.frame_current, index=value.index,
        timeline_time_s=value.timeline_time_s, sample_time_s=value.sample_time_s,
        age_ms=value.age_ms, samples=spec.samples, sampling_hz=spec.sampling_hz,
        timeline_fps=spec.timeline_fps)
    if mode() == 'MAPPO':
        info = modules.same.information(scene)
        assert info and info['index'] == value.index == scene[modules.same.INDEX]
        for key in ('timeline_time_s', 'sample_time_s', 'age_ms'):
            assert abs(info[key] - record[key]) < 1e-7, (key, info, record)
        for bid, obj in enumerate(modules.same.blades(scene), 1):
            original = scene.objects['SplitRecon.B' + str(bid)]
            assert np.array_equal(coordinates(obj), coordinates(original)), (scene.frame_current, bid)
    return record


def complete_framing():
    result = []
    graph = bpy.context.evaluated_depsgraph_get()
    for area, objects in zip(pair(), (source_objects(), right_objects())):
        space, window = area.spaces.active, region(area)
        space.region_3d.update()
        x, y, width, height = modules.preview.available_rectangle(area, window)
        record = dict(region_size=[window.width, window.height], available=[x, y, width, height], objects=[])
        for source in objects:
            obj = source.evaluated_get(graph)
            pixels = [location_3d_to_region_2d(window, space.region_3d,
                       obj.matrix_world @ vertex.co) for vertex in obj.data.vertices]
            assert pixels and all(p is not None for p in pixels), source.name
            box = [min(p.x for p in pixels), max(p.x for p in pixels),
                   min(p.y for p in pixels), max(p.y for p in pixels)]
            assert x + 6 <= box[0] <= box[1] <= x + width - 6, (source.name, box, record)
            assert y + 6 <= box[2] <= box[3] <= y + height - 6, (source.name, box, record)
            record['objects'].append(dict(name=source.name, pixels=box))
        result.append(record)
    return result


class RecordedLayout:
    def __init__(self):
        self.labels, self.operators, self.properties = [], [], []

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: self

    def panel(self, *_args, **_kwargs):
        return self, None

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))

    def prop(self, _data, prop, **kwargs):
        self.properties.append(dict(property=prop, **kwargs))

    def operator(self, name, **kwargs):
        entry = SimpleNamespace(name=name, text=kwargs.get('text', ''))
        self.operators.append(entry)
        return entry


def controls():
    layout = RecordedLayout()
    modules.panel.WFRL_PT_FarmFlex.draw(SimpleNamespace(layout=layout), bpy.context)
    entries = [vars(entry) for entry in layout.operators]
    names = [entry['name'] for entry in entries]
    texts = [entry['text'] for entry in entries]
    assert names.count('wfrl.split_recon_open_example') == 1, entries
    assert names.count('wfrl.split_recon_switch_mode') == 1, entries
    assert 'wfrl.split_recon_same_source' not in names and 'wfrl.split_recon_step' not in names
    assert 'wfrl.split_recon_fit' not in names
    assert names.count('wfrl.split_recon_exit') == 1
    assert names.count('wfrl.clearance_playback') == 1
    assert sorted(e.get('action') for e in entries if e['name'] == 'wfrl.farm_transport') == ['RESET', 'STEP']
    assert not any(any(word in text for word in ('完整取景', '恢复布局', '独立合成', '跟随选中', '上一样本', '下一样本'))
                   for text in texts), entries
    hidden = {'wfrl_clearance_progress', 'wfrl_recon_inspect_blade', 'wfrl_recon_inspect_radius',
              'wfrl_recon_inspect_tau', 'wfrl_recon_inspect_size'}
    assert not hidden.intersection(p['property'] for p in layout.properties), layout.properties
    inspectors = [e for e in entries if e['name'] == 'wfrl.split_recon_inspect']
    if mode() == 'MAPPO':
        assert sorted(e['point'] for e in inspectors) == ['SUPPORTED', 'TIP'], inspectors
        assert names.count('wfrl.split_recon_texture_toggle') == 1
    else:
        assert not inspectors and 'wfrl.split_recon_texture_toggle' not in names
    return dict(labels=layout.labels, operators=entries, properties=layout.properties)


def hud():
    """Read the actual installed draw callback without substituting its text."""
    area = pair()[1]
    records, position = [], [None]
    original = {name: getattr(modules.ui.blf, name) for name in ('position', 'draw')}
    try:
        modules.ui.blf.position = lambda _font, x, y, z: position.__setitem__(0, [x, y, z])
        modules.ui.blf.draw = lambda _font, text: records.append(dict(text=text, position=position[0]))
        with bpy.context.temp_override(window=bpy.context.window, area=area, region=region(area)):
            modules.ui._draw_hud()
    finally:
        for name, function in original.items():
            setattr(modules.ui.blf, name, function)
    texts = [row['text'] for row in records]
    assert any('播放' in text for text in texts), texts
    assert any('样本' in text for text in texts), texts
    assert any('绿' in text and '橙' in text and '估计' in text for text in texts), texts
    assert any('精度未验收' in text for text in texts), texts
    scale = bpy.context.preferences.system.ui_scale
    assert records and all(row['position'][0] <= 20 * scale and
                           0 < row['position'][1] <= 145 * scale for row in records), records
    return records


def immutable():
    scene = bpy.context.scene
    result = dict(geometry={}, textures={})
    for obj in [scene.objects['SplitRecon.B' + str(i)] for i in (1, 2, 3)]:
        keys = obj.data.shape_keys
        assert keys and not keys.use_relative and len(keys.key_blocks) == 601
        assert not keys.animation_data.drivers
        value = hashlib.sha256()
        for key in keys.key_blocks:
            array = np.empty(len(key.data) * 3, dtype=np.float32)
            key.data.foreach_get('co', array)
            value.update(array.tobytes())
        result['geometry'][obj.name] = value.hexdigest()
    for obj in modules.same.blades(scene):
        if obj is None:
            continue
        image = obj.data.materials[0].node_tree.nodes['SurfaceTexture'].image
        assert image and image.packed_file
        uv = np.empty(len(obj.data.uv_layers['atlas'].data) * 2, dtype=np.float32)
        obj.data.uv_layers['atlas'].data.foreach_get('uv', uv)
        result['textures'][obj.name] = dict(packed_sha256=hashlib.sha256(image.packed_file.data).hexdigest(),
                                            uv_sha256=hashlib.sha256(uv.tobytes()).hexdigest(), size=list(image.size))
    return result


def capture(name, *, full=False):
    path = OUT / (name + '.png')
    assert not path.exists(), path
    record = dict(mode=mode(), clock=clock(), navigation=navigation_pair(),
        detail=bool(bpy.context.scene.get(modules.same.DETAIL)), controls=controls(), hud=hud(),
        texture_mode=bpy.context.scene.wfrl_recon_texture_mode, screenshot=str(path))
    if full:
        record['complete_turbine_framing'] = complete_framing()
    assert bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}
    state[name] = record
    (OUT / (name + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')


def start_play(label):
    assert not bpy.context.screen.is_animation_playing
    state[label] = dict(start=signature(), navigation=navigation_pair(), samples=[],
                        deadline=time.monotonic() + 18.)
    operator('clearance_playback')
    assert bpy.context.screen.is_animation_playing


def observe_play(label, *, pause=True):
    record = state[label]
    assert bpy.context.screen.is_animation_playing
    current = signature()
    unchanged(record['navigation'])
    assert not bpy.context.scene.get(modules.same.DETAIL), 'The simplified workflow must never follow a preset'
    assert time.monotonic() < record['deadline'], (label, record, current)
    if current['index'] <= record['start']['index'] or (record['samples'] and
            current['index'] <= record['samples'][-1]['motion']['index']):
        return False
    assert current['frame'] > record['start']['frame']
    assert current['left'] != record['start']['left'], ('Source geometry stopped', label)
    assert current['right'] != record['start']['right'], ('Reconstruction geometry stopped', label)
    record['samples'].append(dict(motion=current, clock=clock(), navigation=navigation_pair()))
    if len(record['samples']) < 2:
        return False
    if pause:
        operator('clearance_playback')
        assert not bpy.context.screen.is_animation_playing
        record['paused'] = signature()
    record.pop('deadline', None)
    return True


def preset(point):
    operator('split_recon_inspect', point=point)
    assert not bpy.context.scene.get(modules.same.DETAIL)
    value = modules.same.ensure(bpy.context.scene)
    target, normal, _up = modules.same._tracked_surface(bpy.context.scene, value)
    area = pair('MAPPO')[1]
    area.spaces.active.region_3d.update()
    projected = location_3d_to_region_2d(region(area), area.spaces.active.region_3d, target)
    assert projected is not None and 8 < projected.x < region(area).width - 8
    assert 8 < projected.y < region(area).height - 8
    assert (area.spaces.active.region_3d.view_location - target).length < .01
    state['preset_' + point] = dict(point=list(target), normal=list(normal), pixels=list(projected),
                                    automatic_follow=False, defect_identification=False)


def new_save(name):
    path = OUT / (name + '.blend')
    assert not path.exists() and path.is_relative_to(OUT)
    assert path != modules.root / 'assets/examples/mappo_reconstruction_split.blend'
    expected = dict(frame=bpy.context.scene.frame_current, mode=mode(),
        texture_mode=bpy.context.scene.wfrl_recon_texture_mode,
        navigation=navigation_pair(), immutable=immutable(), path=str(path))
    masks = [(obj.data, obj.data.show_passepartout, obj.data.passepartout_alpha)
             for obj in bpy.context.scene.objects if obj.type == 'CAMERA'
             and obj.name.startswith(('SplitRecon.', 'SplitTexture.', 'SplitMappoTexture.'))]
    try:
        for data, _enabled, _alpha in masks:
            data.show_passepartout = True
            data.passepartout_alpha = 1.0
        assert bpy.ops.wm.save_as_mainfile(filepath=str(path)) == {'FINISHED'}
    finally:
        for data, enabled, alpha in masks:
            data.show_passepartout = enabled
            data.passepartout_alpha = alpha
    state[name] = expected


def check_reopen(name):
    expected = state[name]
    pair(expected['mode'])
    assert bpy.context.scene.frame_current == expected['frame']
    assert bpy.context.scene.wfrl_recon_texture_mode == expected['texture_mode']
    assert not bpy.context.screen.is_animation_playing
    assert not bpy.context.scene.get(modules.same.DETAIL)
    unchanged(expected['navigation'])
    assert immutable() == expected['immutable']
    assert Path(bpy.data.filepath).resolve() == Path(expected['path'])
    capture(name + '-reopened')


def report(status, error=None):
    result = {key: value for key, value in state.items() if key != 'deadline'}
    result.update(status=status, error=error, blender=bpy.app.version_string,
        addon_module=MODULE, addon_file=modules.addon.__file__ if modules else None,
        actual_module_root=str(modules.root) if modules else None, blend=bpy.data.filepath,
        verification='INSTALLED_VISIBLE_WINDOW_UNIFIED_ENTRY_NATIVE_PLAY_NAVIGATION_SWITCH_SAVE_REOPEN',
        source_regenerated=False, solver_started=False, canonical_asset_modified=False,
        software_behavior_only=True, reconstruction_accuracy_accepted=False,
        texture_or_defect_accuracy_accepted=False,
        manual_review_required=['actual mouse wheel and free mouse orbit', 'sidebar and HUD readability',
                                'visible atlas coverage, seams, striping and defect limitations'],
        visible_window_left_open=os.environ.get('WFRL_QUIT_AFTER_TEST') != '1')
    if modules and 'input' in state:
        result['canonical_asset_sha256_after'] = digest(state['input']['canonical'])
        result['canonical_asset_modified'] = result['canonical_asset_sha256_after'] != state['input']['sha256']
        if result['canonical_asset_modified']:
            result.update(status='FAIL', error=(error or '') + '\nCanonical asset digest changed')
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def tick():
    global modules
    try:
        assert not bpy.app.background and bpy.context.window is not None
        assert time.monotonic() < state['deadline'], ('timeout', state['stage'])
        stage = state['stage']
        if modules and stage != 0:
            scene = bpy.context.scene
            assert not str(scene.get('split_review_status', '')).startswith('ERROR'), scene.get('split_review_status')
            if modules.cameras._LAYOUT_JOB is not None or modules.core._JOBS or scene.get('split_review_status') == 'PREPARING':
                return .25
        if stage == 0:
            load_modules()
            assert not bpy.data.is_dirty, 'Start a clean dedicated window; native unsaved protection must not be bypassed'
            canonical = modules.root / 'assets/examples/mappo_reconstruction_split.blend'
            expected = os.environ.get('WFRL_EXPECTED_GEOMETRY_SHA256', GEOMETRY_SHA256)
            assert digest(canonical) == expected
            state['input'] = dict(canonical=str(canonical), sha256=expected,
                entry_operator='wfrl.split_recon_open_example', startup_blend=bpy.data.filepath)
            operator('split_recon_open_example')
            state['stage'] = 1
            return 3.
        elif stage == 1:
            load_modules()
            if modules.cameras._LAYOUT_JOB is not None or modules.core._JOBS:
                return .25
            assert Path(bpy.data.filepath).resolve() == Path(state['input']['canonical'])
            pair('GEOMETRY')
            assert modules.farm.is_active(bpy.context.scene)
            capture('geometry-unified-entry', full=True)
            state['checks'].append('Unified installed entry opens unchanged canonical geometry; both panes contain tower, nacelle, hub and three blades')
            bpy.context.scene.frame_set(52)
            start_play('geometry-native-play')
        elif stage == 2:
            if not observe_play('geometry-native-play'):
                return .25
            original = bpy.context.scene.frame_current
            operator('farm_transport', action='STEP')
            assert bpy.context.scene.frame_current == original + 1
            operator('farm_transport', action='RESET')
            assert bpy.context.scene.frame_current == bpy.context.scene.frame_start
            bpy.context.scene.frame_set(901)
            state['mode_switch_clock'] = clock()
            state['mode_switch_navigation'] = navigation_pair()
            state['mode_switch_origin'] = display_origin()
            operator('split_recon_switch_mode')
        elif stage == 3:
            pair('MAPPO')
            assert clock() == state['mode_switch_clock']
            unchanged(state['mode_switch_navigation'], cameras=False,
                      right_offset=Vector(display_origin()) - Vector(state['mode_switch_origin']))
            assert not bpy.context.screen.is_animation_playing
            assert len(modules.same.ensure(bpy.context.scene)['sequence'].states) == 601
            assert not bpy.context.scene.get(modules.same.DETAIL)
            state['immutable'] = immutable()
            capture('same-source-complete-turbine', full=True)
            state['checks'].append('Paused geometry to same-source switch preserves clock and navigation; right isolation contains three estimated blades and simulated support structure')
            start_play('same-source-native-play')
        elif stage == 4:
            if not observe_play('same-source-native-play'):
                return .25
            capture('same-source-synchronized-paused', full=True)
            state['checks'].append('Native playback moves both source and reconstruction on the 601 sample, 10 Hz, fixed 60 Hz shared clock')
            state['stage'] = 5
            if pair()[1].spaces.active.region_3d.view_perspective == 'CAMERA':
                return native('view_camera')
            return .35
        elif stage == 5:
            before = navigation(pair()[1])
            center = Vector(modules.same.OFFSET) + Vector(modules.same.ensure(bpy.context.scene)['sequence'].rotor.hub)
            error = (Vector(before['location']) - center).length
            assert error < 80., ('Right orbit center belongs to another turbine', error, before)
            orbit = state.setdefault('native-back-orbit', dict(steps=[], center=list(center)))
            orbit['steps'].append(dict(navigation=before, pivot_error_m=error, framing=complete_framing()))
            if len(orbit['steps']) <= 12:
                return native('view_orbit', type='ORBITRIGHT', angle=math.radians(15.))
            capture('same-source-native-back-view', full=True)
            front = modules.same.camera(bpy.context.scene).matrix_world.to_quaternion() @ Vector((0., 0., -1.))
            from mathutils import Quaternion
            back = Quaternion(before['rotation']) @ Vector((0., 0., -1.))
            orbit['front_back_direction_dot'] = front.normalized().dot(back.normalized())
            assert orbit['front_back_direction_dot'] < -.98, orbit
            state['stage'] = 6
            return native('zoom', delta=3)
        elif stage == 6:
            state['manual_navigation'] = navigation_pair()
            capture('manual-zoom-before-resume')
            start_play('manual-zoom-resume')
        elif stage == 7:
            if not observe_play('manual-zoom-resume'):
                return .25
            unchanged(state['manual_navigation'])
            capture('manual-zoom-after-resume')
            state['checks'].append('Native back orbit and zoom remain unchanged when playback resumes')
            preset('SUPPORTED')
        elif stage == 8:
            capture('supported-one-shot-focus')
            start_play('supported-preset-play')
        elif stage == 9:
            if not observe_play('supported-preset-play'):
                return .25
            state['stage'] = 10
            return native('view_orbit', type='ORBITRIGHT', angle=math.radians(15.))
        elif stage == 10:
            capture('supported-free-orbit')
            preset('TIP')
        elif stage == 11:
            capture('tip-one-shot-focus')
            start_play('tip-preset-play')
        elif stage == 12:
            if not observe_play('tip-preset-play'):
                return .25
            state['checks'].append('SUPPORTED and TIP are one-shot manual observation presets; playback preserves their static views without tracking')
            state['toggle_clock'] = clock()
            state['toggle_original_mode'] = bpy.context.scene.wfrl_recon_texture_mode
            operator('split_recon_texture_toggle')
        elif stage == 13:
            assert bpy.context.scene.wfrl_recon_texture_mode != state['toggle_original_mode']
            assert clock() == state['toggle_clock']
            assert immutable() == state['immutable']
            capture('explicit-enhancement-complete-turbine', full=True)
            preset('TIP')
        elif stage == 14:
            capture('enhanced-manual-observation')
            operator('split_recon_texture_toggle')
        elif stage == 15:
            assert bpy.context.scene.wfrl_recon_texture_mode == state['toggle_original_mode']
            assert clock() == state['toggle_clock']
            assert immutable() == state['immutable']
            capture('explicit-original-complete-turbine', full=True)
            state['checks'].append('Each explicit original/enhanced click fits both complete turbines; packed atlases, UVs and saved geometry remain identical')
            state['roundtrip_clock'] = clock()
            state['roundtrip_navigation'] = navigation_pair()
            state['roundtrip_origin'] = display_origin()
            operator('split_recon_switch_mode')
        elif stage == 16:
            pair('GEOMETRY')
            assert clock() == state['roundtrip_clock']
            unchanged(state['roundtrip_navigation'], cameras=False,
                      right_offset=Vector(display_origin()) - Vector(state['roundtrip_origin']))
            capture('return-to-geometry', full=True)
            operator('split_recon_switch_mode')
        elif stage == 17:
            pair('MAPPO')
            assert clock() == state['roundtrip_clock']
            unchanged(state['roundtrip_navigation'], mode_transfer=True)
            capture('return-to-same-source', full=True)
            start_play('playing-mode-switch')
        elif stage == 18:
            if not observe_play('playing-mode-switch', pause=False):
                return .25
            state['playing_switch_frame'] = bpy.context.scene.frame_current
            state['playing_switch_navigation'] = navigation_pair()
            state['playing_switch_origin'] = display_origin()
            operator('split_recon_switch_mode')
        elif stage == 19:
            pair('GEOMETRY')
            assert bpy.context.screen.is_animation_playing
            assert bpy.context.scene.frame_current >= state['playing_switch_frame']
            unchanged(state['playing_switch_navigation'], cameras=False,
                      right_offset=Vector(display_origin()) - Vector(state['playing_switch_origin']))
            state['playing-geometry-clock'] = clock()
            operator('split_recon_switch_mode')
        elif stage == 20:
            pair('MAPPO')
            assert bpy.context.screen.is_animation_playing
            assert bpy.context.scene.frame_current >= state['playing-geometry-clock']['frame']
            unchanged(state['playing_switch_navigation'], mode_transfer=True)
            operator('clearance_playback')
            assert not bpy.context.screen.is_animation_playing
            state['exit_clock'] = clock()
            state['exit_texture_mode'] = bpy.context.scene.wfrl_recon_texture_mode
            operator('split_recon_exit')
        elif stage == 21:
            assert not modules.core.enabled(bpy.context.scene) and len(views()) == 1
            assert bpy.context.scene.frame_current == state['exit_clock']['frame']
            assert modules.same.active(bpy.context.scene)
            assert bpy.context.scene.wfrl_recon_texture_mode == state['exit_texture_mode']
            operator('split_recon_enter', mode='CURRENT')
        elif stage == 22:
            pair('MAPPO')
            assert clock() == state['exit_clock']
            assert bpy.context.scene.wfrl_recon_texture_mode == state['exit_texture_mode']
            state['checks'].append('Bidirectional mode switches preserve paused or playing state and time; exit and return restore the same source, time and display setting')
            operator('split_recon_texture_toggle')
        elif stage == 23:
            capture('before-save-enhanced-full', full=True)
            preset('SUPPORTED')
        elif stage == 24:
            state['stage'] = 25
            return native('view_orbit', type='ORBITRIGHT', angle=math.radians(15.))
        elif stage == 25:
            capture('before-save-enhanced-local')
            new_save('saved-same-source-local')
        elif stage == 26:
            state['stage'] = 27
            modules = None
            bpy.ops.wm.open_mainfile(filepath=state['saved-same-source-local']['path'], load_ui=True, use_scripts=False)
            return 3.
        elif stage == 27:
            load_modules()
            if modules.cameras._LAYOUT_JOB is not None or modules.core._JOBS:
                return .25
            check_reopen('saved-same-source-local')
            state['checks'].append('Native save/reopen restores same-source mode, enhanced setting, time, packed data and manually orbited local view without an implicit fit')
            operator('split_recon_switch_mode')
        elif stage == 28:
            pair('GEOMETRY')
            new_save('saved-geometry-local')
        elif stage == 29:
            state['stage'] = 30
            modules = None
            bpy.ops.wm.open_mainfile(filepath=state['saved-geometry-local']['path'], load_ui=True, use_scripts=False)
            return 3.
        elif stage == 30:
            load_modules()
            if modules.cameras._LAYOUT_JOB is not None or modules.core._JOBS:
                return .25
            check_reopen('saved-geometry-local')
            state['checks'].append('Native save/reopen also restores geometry mode and navigation without replacing the current mode')
            operator('split_recon_switch_mode')
        elif stage == 31:
            pair('MAPPO')
            assert bpy.context.scene.wfrl_recon_texture_mode == state['saved-same-source-local']['texture_mode']
            assert immutable() == state['immutable']
            # Leave a complete enhanced turbine visible for actual mouse review.
            modules.core.fit(bpy.context)
        elif stage == 32:
            capture('final-same-source-enhanced-complete', full=True)
            assert not bpy.context.screen.is_animation_playing
            assert bpy.context.preferences.view.smooth_view == state['smooth_view_preference']
            report('PASS')
            print('RECONSTRUCTION_WORKFLOW_WINDOW_PASS', str(REPORT), flush=True)
            if os.environ.get('WFRL_QUIT_AFTER_TEST') == '1':
                bpy.ops.wm.quit_blender()
            return None
        state['stage'] += 1
        return .5
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        report('FAIL', error)
        if os.environ.get('WFRL_QUIT_AFTER_TEST') == '1':
            bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=3., persistent=True)
