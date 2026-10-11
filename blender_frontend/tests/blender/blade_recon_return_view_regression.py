"""Native independent texture inspection and exact return-view persistence.

Run create and then reopen in fresh Blender processes. This uses a short copy
of existing saved recon samples, never invokes a solver or writes result inputs.
WFRL_ADDON_MODULE / WFRL_EXPECTED_ADDON_ROOT distinguish source and install.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/blade-recon-return-view')).resolve()
OUT.mkdir(parents=True, exist_ok=True)
MODE = os.environ.get('WFRL_BLADE_RECON_MODE', 'create')
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
report = dict(status='RUNNING', mode=MODE, blender=bpy.app.version_string,
              scope='native saved viewport restoration and appearance APIs; software display only', checks={})
checks = report['checks']


def digest(blades):
    values = []
    for obj in blades:
        data = obj.data
        image = data.materials[0].node_tree.nodes['SurfaceTexture'].image
        buffers = []
        for collection, attribute, width in ((data.vertices, 'co', 3),
                                             (data.uv_layers['atlas'].data, 'uv', 2),
                                             (data.color_attributes['evidence'].data, 'color', 4)):
            array = np.empty(len(collection) * width, np.float32)
            collection.foreach_get(attribute, array)
            buffers.append(hashlib.sha256(array.tobytes()).hexdigest())
        values.append((buffers, hashlib.sha256(image.packed_file.data).hexdigest()))
    return values


try:
    if MODULE == 'wfrl_blender':
        sys.path.insert(0, str(ROOT / 'blender_frontend'))
    addon = importlib.import_module(MODULE)
    report['addon_file'] = str(Path(addon.__file__).resolve())
    expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
    if expected_root:
        assert Path(addon.__file__).resolve().is_relative_to(Path(expected_root).resolve())
    if not hasattr(bpy.types.Scene, 'wfrl_recon_texture_mode'):
        addon.register()
    review = importlib.import_module(MODULE + '.blade_recon_review')
    inspection = importlib.import_module(MODULE + '.blade_recon_inspection')
    if MODE == 'create':
        original = bpy.context.scene
        original.name = 'ReturnView.OriginalFarm'
        original.frame_end = 100
        original.frame_set(37)
        marker = bpy.data.objects.new('ReturnView.Sentinel', None)
        original.collection.objects.link(marker)
        marker['preserve'] = 'original farm'
        for area in review._view_areas(bpy.context):
            space = area.spaces.active
            space.lens = 32
            space.clip_start, space.clip_end = .15, 850
            space.show_region_ui = False
            space.use_local_camera = True
            space.camera = original.objects.get('Camera')
            space.region_3d.view_perspective = 'PERSP'
            space.region_3d.view_location = Vector((2., 3., 4.))
            space.region_3d.view_distance = 11
            space.shading.type = 'SOLID'
            space.shading.color_type = 'MATERIAL'
            space.shading.background_type = 'WORLD'
            space.shading.show_shadows = True
            space.overlay.show_floor = True
            space.overlay.show_axis_x = True
            space.overlay.show_axis_y = True
        before = review.snapshot_viewports(bpy.context)
        assert before['areas'], 'native screen must contain a VIEW_3D'
        original['return_view_expected'] = json.dumps(before)
        data = json.loads((ROOT / 'blade_recon0.2/out/synth_tex/recon.json').read_text())
        data['frames'] = [data['frames'][index] for index in (0, 40, 80)]
        fixture = OUT / 'saved-input-copy.json'
        fixture.write_text(json.dumps(data))
        for cycle in range(3):
            scene = review.load(bpy.context, fixture, texture_path=ROOT / 'blade_recon0.2/out/synth_tex/texture')
            value = review.session(scene)
            baseline = digest(value['blades'])
            assert review.toggle_texture_mode(scene) == 'FALSE_COLOR'
            assert bpy.ops.wfrl.blade_recon_texture_toggle() == {'FINISHED'}
            assert scene.wfrl_recon_texture_mode == 'ORIGINAL'
            assert digest(value['blades']) == baseline
            checks['one_click_modes_preserve_geometry_uv_evidence_packed_images'] = True
            unchanged = review.snapshot_viewports(bpy.context)
            focus = inspection.focus(bpy.context, value['sequence'], value['blades'], areas=[], index=1)
            assert focus['views'] == 0 and review.snapshot_viewports(bpy.context) == unchanged
            # Independent right-side placement must use object world transforms.
            value['blades'][0].location.x = 10000
            bpy.context.view_layer.update()
            shifted = inspection.focus(bpy.context, value['sequence'], value['blades'], areas=[], index=1)
            assert abs(shifted['target'][0] - focus['target'][0] - 10000) < .001
            value['blades'][0].location.x = 0
            bpy.context.view_layer.update()
            checks['split_focus_uses_explicit_sample_area_list_and_world_transform'] = True
            review.set_view(bpy.context, 'OVERVIEW')
            result = review.inspect_view(bpy.context)
            assert result['views'] == len(before['areas'])
            assert review.snapshot_viewports(bpy.context) != before
            assert bpy.ops.wfrl.blade_recon_return() == {'FINISHED'}
            assert bpy.context.scene == original
            assert review.snapshot_viewports(bpy.context) == before
            assert original.frame_current == 37 and original.frame_end == 100
            assert marker in original.objects[:]
            if cycle < 2:
                review.remove(scene)
        checks['three_overview_inspection_return_cycles_restore_exact_original_view_and_data'] = True
        # A failure after switching and mutating the shared viewport must roll back.
        actual_set_view = review.set_view
        def fail_after_view(*args, **kwargs):
            actual_set_view(*args, **kwargs)
            raise ValueError('intentional post-view regression failure')
        review.set_view = fail_after_view
        try:
            count = len(bpy.data.scenes)
            try:
                review.load(bpy.context, fixture, texture_path=ROOT / 'blade_recon0.2/out/synth_tex/texture')
                raise AssertionError('expected injected failure')
            except ValueError as exc:
                assert str(exc) == 'intentional post-view regression failure'
            assert len(bpy.data.scenes) == count and bpy.context.scene == original
            assert review.snapshot_viewports(bpy.context) == before
        finally:
            review.set_view = actual_set_view
        checks['post_switch_load_failure_restores_original_scene_and_view'] = True
        bpy.context.window.scene = scene
        review.inspect_view(bpy.context)
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'return-view.blend'))
        fixture.unlink()
        checks['inspection_saved_with_serialized_original_view_and_external_copy_removed'] = True
    elif MODE == 'reopen':
        scene = next(scene for scene in bpy.data.scenes if review.active(scene))
        bpy.context.window.scene = scene
        assert not Path(scene['blade_recon_source_path']).exists()
        original = bpy.data.scenes[scene['blade_recon_return_scene']]
        before = json.loads(original['return_view_expected'])
        review._tick()
        assert not scene.get('blade_recon_error'), scene.get('blade_recon_error')
        review.set_view(bpy.context, 'FRONT')
        review.inspect_view(bpy.context)
        result = review.return_to_original(bpy.context)
        assert result['restored_views'] == len(before['areas'])
        assert review.snapshot_viewports(bpy.context) == before
        assert original.frame_current == 37 and original.frame_end == 100
        assert original.objects['ReturnView.Sentinel']['preserve'] == 'original farm'
        checks['fresh_process_return_restores_original_view_timeline_objects'] = True
    else:
        raise ValueError('Unsupported mode: ' + MODE)
    report['status'] = 'PASS'
except Exception:
    report['status'] = 'FAIL'
    report['error'] = traceback.format_exc()
    raise
finally:
    (OUT / (MODE + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('BLADE_RECON_RETURN_VIEW_' + MODE.upper() + '_' + report['status'])
