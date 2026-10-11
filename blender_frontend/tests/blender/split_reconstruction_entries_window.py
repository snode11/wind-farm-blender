"""Visible installed-extension regression for the two reconstruction entries.

Open an additive copy of the packaged geometry .blend in a visible window.
WFRL_ADDON_MODULE and WFRL_EXPECTED_ADDON_ROOT must identify the installation.
WFRL_TEST_OUTPUT is an additive output directory. At geometry and texture
screenshots, optional continue-geometry / continue-texture files release the
window early; otherwise WFRL_HOLD_SECONDS (default 0) controls the pause.
Uses saved geometry and the bundled texture sample, without solving or rendering.
"""
import importlib
import json
import os
from pathlib import Path
import time
import traceback
from types import SimpleNamespace

import bpy

MODULE = os.environ['WFRL_ADDON_MODULE']
EXPECTED_ROOT = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
HOLD_SECONDS = float(os.environ.get('WFRL_HOLD_SECONDS', '0'))
addon = importlib.import_module(MODULE)
split = importlib.import_module(MODULE + '.split_reconstruction')
surface = importlib.import_module(MODULE + '.split_surface_texture')
ui = importlib.import_module(MODULE + '.split_reconstruction_ui')
cameras = importlib.import_module(MODULE + '.cameras')
state = {'stage': 0, 'checks': [], 'deadline': time.monotonic() + 300}


def views():
    return sorted((area for area in bpy.context.window.screen.areas
                   if area.type == 'VIEW_3D'), key=lambda area: area.x)


def operator(name, **kwargs):
    area = views()[0]
    region = next(region for region in area.regions if region.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        result = getattr(bpy.ops.wfrl, name)(**kwargs)
    assert result == {'FINISHED'}, (name, result)


def geometry_signature():
    scene = bpy.context.scene
    result = {}
    for blade_id in (1, 2, 3):
        obj = scene.objects[f'SplitRecon.B{blade_id}']
        keys = obj.data.shape_keys
        assert keys is not None and not keys.use_relative
        assert len(keys.key_blocks) == 601, len(keys.key_blocks)
        assert not keys.animation_data or not keys.animation_data.drivers
        result[obj.name] = {'pointer': obj.as_pointer(), 'keys': len(keys.key_blocks),
                            'vertices': len(obj.data.vertices),
                            'interpolation_types': sorted({key.interpolation for key in keys.key_blocks})}
    return result


def pair(textured):
    scene = bpy.context.scene
    areas = views()
    assert len(areas) == 2, len(areas)
    left, right = [area.spaces.active for area in areas]
    assert split.enabled(scene) and scene.get('split_review_status') == 'READY'
    expected = surface.camera(scene) if textured else scene.objects['SplitRecon.Camera']
    assert bool(surface.active(scene)) is textured
    assert right.camera == expected and right.use_local_camera
    assert right.local_view is not None and left.local_view is None
    for blade_id in (1, 2, 3):
        legacy = scene.objects[f'SplitRecon.B{blade_id}']
        assert legacy.local_view_get(right) is not textured
        source = scene.objects[f'WFRL.Turbine.T1.Blade{blade_id}']
        assert not source.local_view_get(right)
    if textured:
        assert len(surface.ensure(scene)['sequence'].states) == 120
        assert all(obj.local_view_get(right) for obj in surface.objects(scene))
    else:
        assert not surface.owned(scene)
    assert geometry_signature() == state['geometry_signature']
    return {'left_camera': left.camera.name if left.camera else None,
            'right_camera': right.camera.name, 'areas': [area.width for area in areas],
            'frame': scene.frame_current, 'texture_sample': scene.get(surface.INDEX)}


class RecordedLayout:
    """Capture the installed draw function's operator properties and labels."""
    def __init__(self):
        self.labels = []
        self.operators = []

    def box(self):
        return self

    def row(self, **_kwargs):
        return self

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))

    def operator(self, name, **kwargs):
        entry = SimpleNamespace(name=name, text=kwargs.get('text', ''))
        self.operators.append(entry)
        return entry


def controls():
    layout = RecordedLayout()
    ui.draw_controls(layout, bpy.context)
    entries = [vars(entry) for entry in layout.operators]
    restore = [entry for entry in entries if entry['text'] == '恢复布局']
    if split.enabled(bpy.context.scene):
        assert len(restore) == 1 and restore[0].get('mode') == 'CURRENT', restore
    return {'labels': layout.labels, 'operators': entries}


def capture(name, textured):
    details = pair(textured)
    details['controls'] = controls()
    details['screenshot'] = str(OUT / (name + '.png'))
    bpy.ops.screen.screenshot(filepath=details['screenshot'])
    (OUT / (name + '-ready.json')).write_text(json.dumps(details, ensure_ascii=False, indent=2))
    state[name] = details
    state['hold_until'] = time.monotonic() + HOLD_SECONDS
    print('RECON_ENTRY_WINDOW_READY', name, flush=True)


def holding(name):
    return (not (OUT / ('continue-' + name)).exists()
            and time.monotonic() < state['hold_until'])


def write_report(status, error=None):
    report = {key: value for key, value in state.items() if key not in {'deadline', 'hold_until'}}
    report.update(status=status, addon_module=MODULE, addon_file=addon.__file__,
                  blender=bpy.app.version_string, blend=bpy.data.filepath,
                  verification='INSTALLED_VISIBLE_WINDOW_OPERATORS_AND_SCREENSHOTS',
                  source_regenerated=False, solver_started=False, error=error)
    (OUT / 'entry-window-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


def tick():
    try:
        assert not bpy.app.background and bpy.context.window is not None
        assert time.monotonic() < state['deadline'], ('timeout', state['stage'])
        scene = bpy.context.scene
        assert not str(scene.get('split_review_status', '')).startswith('ERROR'), scene.get('split_review_status')
        if cameras._LAYOUT_JOB is not None or split._JOBS:
            return .2
        stage = state['stage']
        if stage == 0:
            assert MODULE.startswith('bl_ext.') and Path(addon.__file__).resolve().parent == EXPECTED_ROOT
            assert bpy.ops.wfrl.split_recon_open_example.get_rna_type().name == '打开几何重建示例…'
            assert bpy.ops.wfrl.split_recon_enter.get_rna_type().name == '进入纹理对照'
            default = bpy.ops.wfrl.split_recon_enter.get_rna_type().properties['mode'].default
            assert default == 'TEXTURE', default
            state['operator_labels'] = {'open_example': '打开几何重建示例…',
                                        'enter': '进入纹理对照', 'default_mode': default}
            assert scene.get('split_reconstruction_review') and not surface.active(scene)
            assert scene['split_reconstruction_samples'] == 601
            state['geometry_signature'] = geometry_signature()
            ui._pause_all_playback(bpy.context)
            scene.frame_set(52)
            state['checks'].append('Packaged example starts as original 601-sample geometry, with distinct installed entry labels')
            split.enter(bpy.context)
        elif stage == 1:
            assert scene.frame_current == 52 and not bpy.context.screen.is_animation_playing
            capture('geometry', False)
        elif stage == 2:
            if holding('geometry'):
                return .2
            operator('split_recon_step', direction=1)
            assert scene.frame_current == 55, scene.frame_current
            pair(False)
            state['checks'].append('Geometry sample step moves shared MAPPO frame 52 to next actual sample frame55')
            operator('split_recon_fit')
        elif stage == 3:
            pair(False)
            assert scene.frame_current == 55
            operator('split_recon_enter', mode='CURRENT')
        elif stage == 4:
            pair(False)
            assert scene.frame_current == 55
            state['checks'].append('Geometry fit and CURRENT layout reset preserve original camera, shape keys and frame')
            operator('split_recon_exit')
        elif stage == 5:
            assert not split.enabled(scene) and len(views()) == 1 and not surface.active(scene)
            assert scene.frame_current == 55
            state['exit_geometry_controls'] = controls()
            return_entries = [entry for entry in state['exit_geometry_controls']['operators']
                              if entry['text'] == '返回几何对照']
            assert len(return_entries) == 1 and return_entries[0].get('mode') == 'CURRENT'
            operator('split_recon_enter', mode='CURRENT')
        elif stage == 6:
            pair(False)
            assert scene.frame_current == 55
            state['checks'].append('Exit and CURRENT return preserve geometry and shared timeline')
            operator('split_recon_enter', mode='TEXTURE')
        elif stage == 7:
            pair(True)
            assert scene.frame_current == 55
            state['checks'].append('Explicit TEXTURE entry creates 120 independent samples and a different right camera while preserving left frame')
            capture('texture', True)
        elif stage == 8:
            if holding('texture'):
                return .2
            initial = scene[surface.INDEX]
            operator('split_recon_step', direction=1)
            assert scene[surface.INDEX] == min(initial + 1, 119)
            assert scene.frame_current == 55
            pair(True)
            state['checks'].append('Texture sample step changes only the independent right sample')
            operator('split_recon_enter', mode='CURRENT')
        elif stage == 9:
            pair(True)
            assert scene.frame_current == 55
            state['checks'].append('CURRENT layout reset also preserves the active texture comparison')
            write_report('PASS')
            print('RECONSTRUCTION_ENTRIES_WINDOW_PASS', flush=True)
            bpy.ops.wm.quit_blender()
            return None
        state['stage'] += 1
        return 1.
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        write_report('FAIL', error)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=3.)
