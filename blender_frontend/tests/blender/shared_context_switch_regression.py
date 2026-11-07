"""Native reconstruction exit and transactional shared-mesh model switching."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
addon_root = Path(addon.__file__).resolve().parent
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert addon_root == Path(expected).resolve()
assert not bpy.app.background, 'Requires an independent visible window'
if MODULE == 'wfrl_blender':
    addon.register()
else:
    assert MODULE in bpy.context.preferences.addons
farm = importlib.import_module(MODULE + '.farm_flex')
shared = importlib.import_module(MODULE + '.split_turbine_context')
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)
result = dict(scope='Independent visible Blender process; scripted product operators',
              blender=bpy.app.version_string, addon_file=addon.__file__, rows=[])
state = dict(step=0, deadline=time.monotonic() + 240)


def row(label):
    scene = bpy.context.scene
    for key, prefix, _offset in shared._SAVED_GROUPS:
        for name in scene.get(key, ()):
            obj = scene.objects[name]
            source = scene.objects[obj['split_turbine_context_source']]
            assert obj.data == source.data, name
    value = dict(label=label, model=scene.wfrl_blade_model, meshes=len(bpy.data.meshes),
                 owned_orphans=[mesh.name for mesh in bpy.data.meshes
                                if mesh.get('wfrl_farm_flex_owned') and mesh.users == 0])
    result['rows'].append(value)
    return value


def tick():
    try:
        assert time.monotonic() < state['deadline'], 'Window workflow timed out'
        if state['step'] == 0:
            assert not bpy.data.is_dirty, 'Start in a clean independent process'
            assert bpy.ops.wfrl.split_recon_open_example() == {'FINISHED'}
            state['step'] = 1
            return 2.
        scene = bpy.context.scene
        if state['step'] == 1:
            if scene.get('split_review_status') != 'READY':
                return .5
            assert farm.is_active(scene)
            row('comparison_ready')
            assert bpy.ops.wfrl.split_recon_exit() == {'FINISHED'}
            state['step'] = 2
            return 1.
        if state['step'] == 2:
            if scene.get('split_review_status') != 'OFF':
                return .5
            state['baseline'] = row('comparison_exited')
            state['step'] = 3
        if 3 <= state['step'] < 23:
            model = 'rootkink' if state['step'] % 2 else 'nrel5mw'
            scene.wfrl_blade_model = model
            assert farm.is_active(scene) and farm.active_for(scene).model == model
            assert not scene.get('wfrl_blade_model_error'), scene.get('wfrl_blade_model_error')
            value = row('switch_' + str(state['step'] - 2))
            assert value['meshes'] == state['baseline']['meshes'], value
            assert value['owned_orphans'] == state['baseline']['owned_orphans'], value
            state['step'] += 1
            return .05
        original_replace = farm._MeshBindings.replace
        failed = []
        def failing_replace(bindings, obj, mesh):
            original_replace(bindings, obj, mesh)
            if obj.name == 'SplitRecon.Reference.Tower' and not failed:
                failed.append(True)
                raise ValueError('Injected shared structure binding failure')
        farm._MeshBindings.replace = failing_replace
        try:
            scene.wfrl_blade_model = 'rootkink'
        finally:
            farm._MeshBindings.replace = original_replace
        assert failed and farm.active_for(scene).model == 'nrel5mw'
        assert 'Injected shared structure binding failure' in scene['wfrl_blade_model_error']
        value = row('shared_binding_failure_recovered')
        assert value['meshes'] == state['baseline']['meshes'], value
        assert value['owned_orphans'] == state['baseline']['owned_orphans'], value
        saved = output / 'shared-context.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(saved))
        bpy.ops.wm.open_mainfile(filepath=str(saved))
        assert farm.is_active(bpy.context.scene)
        value = row('saved_reopened')
        assert value['owned_orphans'] == state['baseline']['owned_orphans'], value
        result['sha256'] = {name: hashlib.sha256((addon_root / name).read_bytes()).hexdigest()
                           for name in ('farm_flex.py', 'split_turbine_context.py')}
        result['status'] = 'PASS'
        (output / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print('SHARED_CONTEXT_SWITCH_PASS ' + json.dumps(result), flush=True)
        bpy.ops.wm.quit_blender()
    except Exception:
        result['status'], result['error'] = 'FAIL', traceback.format_exc()
        (output / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(result['error'], flush=True)
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=2., persistent=True)
