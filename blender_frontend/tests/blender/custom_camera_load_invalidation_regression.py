"""File loads preserve scene-change invalidation and release preview callbacks.

Real file loading, module reload and dependency-graph updates drive Preview.refresh.
A render adapter records its requests without allocating a GPU framebuffer.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy


assert bpy.app.background, 'Run in an independent background factory-startup process.'
ROOT = Path(__file__).resolve().parents[3]
assert os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender') == 'wfrl_blender'
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
preview = importlib.import_module('wfrl_blender.custom_camera_preview')
panel = importlib.import_module('wfrl_blender.panels.custom_cameras')
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory.'
OUT.mkdir(parents=True)
REPORT = {
    'status': 'RUNNING', 'blender': bpy.app.version_string,
    'addon_file': addon.__file__, 'background': bpy.app.background,
    'method': 'Real open_mainfile, module reload and mesh mutation; render adapter without GPU drawing.',
    'source_sha256': {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                      for module in (preview, panel)},
    'checks': {}, 'failures': {},
}
original_render = preview.render_group
owned_callbacks = {preview.changed}


@bpy.app.handlers.persistent
def unrelated_handler(*_args):
    pass


def own_callback_count():
    return sum(handler in owned_callbacks for handler in bpy.app.handlers.depsgraph_update_post)


def check(name, condition, details=None):
    REPORT['checks'][name] = bool(condition)
    if not condition:
        REPORT['failures'][name] = details


class Image:
    width = height = 1

    def free(self):
        pass


def verify_refresh(label):
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    renders = []

    def record_render(context, _cameras, _long_edge, target, _fast):
        renders.append(target.state_cache.hash(context.scene, context.evaluated_depsgraph_get(),
                                               panel.session_identity(context.scene)))
        return {1: Image()}

    preview.render_group = record_render
    with bpy.context.temp_override(window=window, area=area, region=region):
        renderer = preview.Preview()
        renderer.refresh(bpy.context, [])
        assert not renderer.error and len(renders) == 1
        old_key = dict(renderer.key)
        revision = preview._REVISION
        mesh = bpy.context.scene.objects['WFRL.Turbine.LoadFixture'].data
        mesh.vertices[0].co.x += .125
        mesh.update()
        bpy.context.view_layer.update()
        check(f'{label}_native_mutation_increments_revision', preview._REVISION > revision,
              {'before': revision, 'after': preview._REVISION})
        renderer.last = float('-inf')
        renderer.refresh(bpy.context, [])
        check(f'{label}_refresh_invalidates', len(renders) == 2 and renderer.key != old_key,
              {'render_requests': len(renders), 'old_revision': old_key['revision'],
               'new_revision': renderer.key['revision'], 'error': renderer.error})
        check(f'{label}_geometry_fingerprint_changes', len(renders) == 2 and renders[0] != renders[1],
              {'fingerprints': renders})
        renderer.free()


try:
    addon.register()
    bpy.data.objects['Cube'].name = 'WFRL.Turbine.LoadFixture'
    fixture = OUT / 'preview-load.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(fixture))
    check('initial_callback_once', bpy.app.handlers.depsgraph_update_post.count(preview.changed) == 1)
    for cycle in range(1, 4):
        bpy.ops.wm.open_mainfile(filepath=str(fixture))
        count = bpy.app.handlers.depsgraph_update_post.count(preview.changed)
        check(f'load_{cycle}_callback_once', count == 1, {'callback_count': count})
        verify_refresh(f'load_{cycle}')
        panel.migrate_loaded()
        panel.migrate_loaded()
        count = bpy.app.handlers.depsgraph_update_post.count(preview.changed)
        check(f'load_{cycle}_migration_idempotent', count == 1, {'callback_count': count})
    bpy.app.handlers.depsgraph_update_post.append(unrelated_handler)
    for cycle in range(1, 3):
        preview.render_group = original_render
        preview = importlib.reload(preview)
        owned_callbacks.add(preview.changed)
        original_render = preview.render_group
        if cycle == 2:
            preview.unregister_change_handler()
            count = own_callback_count()
            check('reload_before_register_unregisters_all_owned_callbacks', count == 0,
                  {'callback_count': count})
        preview.register_change_handler()
        preview.register_change_handler()
        panel.migrate_loaded()
        count = own_callback_count()
        check(f'reload_{cycle}_exactly_one_current_callback',
              count == 1 and preview.changed in bpy.app.handlers.depsgraph_update_post,
              {'callback_count': count})
        check(f'reload_{cycle}_preserves_unrelated_callback',
              bpy.app.handlers.depsgraph_update_post.count(unrelated_handler) == 1)
        verify_refresh(f'reload_{cycle}')
    preview.render_group = original_render
    preview = importlib.reload(preview)
    owned_callbacks.add(preview.changed)
    original_render = preview.render_group
    REPORT['status'] = 'FAIL' if REPORT['failures'] else 'PASS'
except Exception:
    REPORT['status'] = 'ERROR'
    REPORT['error'] = traceback.format_exc()
    raise
finally:
    preview.render_group = original_render
    addon.unregister()
    check('unregister_removes_callback', own_callback_count() == 0,
          {'callback_count': own_callback_count()})
    check('unregister_preserves_unrelated_callback',
          bpy.app.handlers.depsgraph_update_post.count(unrelated_handler) == 1)
    if unrelated_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(unrelated_handler)
    check('unregister_releases_renderers', not preview._RENDERERS)
    if REPORT['failures'] and REPORT['status'] == 'PASS':
        REPORT['status'] = 'FAIL'
    (OUT / 'checks.json').write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + '\n')
    print('CUSTOM_CAMERA_LOAD_INVALIDATION ' + json.dumps(REPORT, ensure_ascii=False), flush=True)
assert REPORT['status'] == 'PASS', REPORT.get('failures', REPORT.get('error'))
print('CUSTOM_CAMERA_LOAD_INVALIDATION_REGRESSION_PASS', flush=True)
