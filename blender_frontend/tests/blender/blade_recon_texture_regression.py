"""v0.2 independent scene, packed textures and fresh-process lifecycle tests.

WFRL_BLADE_RECON_MODE=create|reopen|standalone-reopen. The create mode saves a
file then removes its disposable external inputs. Reopen modes are new Blender
processes; standalone-reopen deliberately imports no addon. WFRL_ADDON_MODULE
and WFRL_EXPECTED_ADDON_ROOT distinguish source, isolated ZIP and daily install.
"""
import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import traceback

import bpy
from bpy.app.handlers import persistent
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/blade-recon-texture-regression')).resolve()
OUT.mkdir(parents=True, exist_ok=True)
MODE = os.environ.get('WFRL_BLADE_RECON_MODE', 'create')
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
report = {'status': 'RUNNING', 'mode': MODE, 'blender': bpy.app.version_string,
          'scope': 'native background replay and file persistence; synthetic geometry display only',
          'checks': {}, 'frames': []}
checks = report['checks']


def output():
    (OUT / (MODE+'.json')).write_text(json.dumps(report, indent=2, ensure_ascii=False))


def co(obj, evaluated=False):
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data if evaluated else obj.data
    result = np.empty(len(mesh.vertices)*3, np.float32)
    mesh.vertices.foreach_get('co', result)
    return result.reshape(-1, 3)


def original_model():
    spec = importlib.util.spec_from_file_location('v02_texture_test_model', ROOT/'blade_recon0.2/model.py')
    model = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = model
    spec.loader.exec_module(model)
    return model


def check_images(scene):
    blades = sorted([o for o in scene.objects if o.get('blade_recon_role') == 'estimate'],
                    key=lambda o:o['blade_recon_blade'])
    data = json.loads(bpy.data.texts[scene['texture_text']].as_string())
    H, W = data['atlas']['H'], data['atlas']['W']
    for b, obj in enumerate(blades):
        nodes = obj.data.materials[0].node_tree.nodes
        image = nodes['SurfaceTexture'].image
        assert tuple(image.size) == (W, H) and image.packed_file
        assert hashlib.sha256(image.packed_file.data).hexdigest() == data['files'][b]['sha256']
        assert nodes['CircumferenceWrap'].operation == 'FRACT'
        assert nodes['SpanRootClamp'].operation == 'MINIMUM'
        assert nodes['SpanTipClamp'].operation == 'MAXIMUM'
        assert abs(nodes['SpanRootClamp'].inputs[1].default_value-(1-.5/H)) < 1e-7
        assert abs(nodes['SpanTipClamp'].inputs[1].default_value-.5/H) < 1e-7
        # Root/tip contrast regression for the actual material graph: REPEAT
        # would sample 50 at V=0, but the connected clamp yields tip value 0.
        v = max(min(0., nodes['SpanRootClamp'].inputs[1].default_value),
                nodes['SpanTipClamp'].inputs[1].default_value)
        assert abs(v*H-.5) < 1e-7
        uvs = np.empty(len(obj.data.uv_layers['atlas'].data)*2, np.float32)
        obj.data.uv_layers['atlas'].data.foreach_get('uv', uvs)
        assert np.isfinite(uvs).all() and uvs.min() >= 0 and uvs.max() <= 1
    checks['three_rgba_images_packed_hashes_and_atlas_uv'] = True
    checks['native_shader_circumference_periodic_span_pixel_center_clamp'] = True
    return blades


try:
    if MODE == 'standalone-reopen':
        scene = next(s for s in bpy.data.scenes if s.get('blade_recon_portable_geometry'))
        assert not Path(scene['blade_recon_source_path']).exists()
        assert not Path(scene['blade_recon_texture_source_path']).exists()
        assert 'wfrl_blender' not in sys.modules
        assert not any(getattr(f, '__module__', '').endswith('blade_recon_review')
                       for f in bpy.app.handlers.frame_change_post)
        checks['no_addon_or_algorithm_viewer_executed'] = True
        data = json.loads(bpy.data.texts[scene['recon_text']].as_string())
        model = original_model()
        rotor = model.Rotor(model.TurbineConfig.from_dict(data['turbine']))
        blades = check_images(scene)
        first = None
        for frame in (1, 61, 120, 17, 18, 17):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            expected = rotor.forward(np.asarray(data['frames'][frame-1]['state']))[0]
            errors = [float(np.max(np.abs(co(obj, True)-expected[b].reshape(-1, 3))))
                      for b, obj in enumerate(blades)]
            assert max(errors) < 1e-4, errors
            for obj in blades:
                assert len(obj.data.shape_keys.key_blocks) == len(data['frames'])
                assert obj.data.shape_keys.use_relative is False
                nodes = obj.data.materials[0].node_tree.nodes
                assert nodes['PortableEvidence'].image.packed_file
                assert abs(nodes['EvidenceSampleTime'].outputs[0].default_value-
                           ((frame-1+.5)/len(data['frames']))) < 1e-6
                assert not obj.hide_viewport
            if first is None:
                first = co(blades[0], True).copy()
            report['frames'].append({'frame': frame, 'max_geometry_error_m': max(errors)})
        assert np.linalg.norm(first-co(blades[0], True)) > 1
        checks['native_absolute_geometry_and_evidence_sample_animation'] = True
    else:
        if MODULE == 'wfrl_blender':
            sys.path.insert(0, str(ROOT/'blender_frontend'))
        addon = importlib.import_module(MODULE)
        report['addon_file'] = str(Path(addon.__file__).resolve())
        expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
        if expected_root:
            assert Path(addon.__file__).resolve().is_relative_to(Path(expected_root).resolve())
        if not hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'):
            addon.register()
        review = importlib.import_module(MODULE+'.blade_recon_review')
        reader = importlib.import_module(MODULE+'.blade_recon_data')
        if MODE == 'create':
            original = bpy.context.scene
            original.name = 'V02.OriginalFarm'
            sentinel = bpy.data.objects.new('V02.FarmSentinel', None)
            original.collection.objects.link(sentinel)
            sentinel['preserve'] = 'original farm'
            before_objects = set(bpy.data.objects.keys())
            original.frame_set(37)
            original.frame_end = 98
            calls = []
            @persistent
            def unrelated(scene, *_):
                calls.append(scene.frame_current)
            bpy.app.handlers.frame_change_pre.append(unrelated)
            fixture = OUT/'unavailable-external-inputs'
            fixture.mkdir(exist_ok=True)
            data = json.loads((ROOT/'blade_recon0.2/out/synth_tex/recon.json').read_text())
            base_cameras = json.loads((ROOT/'blade_recon0.2/data/synth_tex/cameras.json').read_text())['cameras']
            for i, frame in enumerate(data['frames']):
                frame['cameras'] = []
                for camera in base_cameras:
                    entry = {k: copy.deepcopy(camera[k]) for k in ('name','W','H','K')}
                    entry['T_cv_from_model'] = copy.deepcopy(camera['T_cv_from_world'])
                    entry['T_cv_from_model'][0][3] += i*.01
                    entry['K'][0][0] += i*.1
                    frame['cameras'].append(entry)
            (fixture/'recon.json').write_text(json.dumps(data))
            texture = fixture/'texture'
            texture.mkdir(exist_ok=True)
            for name in ('texture.json','blade1_tex.png','blade2_tex.png','blade3_tex.png'):
                shutil.copy2(ROOT/'blade_recon0.2/out/synth_tex/texture'/name, texture/name)
            scene = review.load(bpy.context, fixture/'recon.json', texture_path=texture)
            assert scene != original and before_objects.issubset(set(bpy.data.objects.keys()))
            assert original.frame_current == 37 and original.frame_end == 98
            assert unrelated in bpy.app.handlers.frame_change_pre
            checks['original_farm_objects_timeline_and_foreign_callback_preserved'] = True
            # Invalid explicit input must fail before creating a scene or
            # silently falling back to evidence-only materials.
            scene_count = len(bpy.data.scenes)
            try:
                review.load(bpy.context, fixture/'recon.json', texture_path=OUT/'missing-texture')
                raise AssertionError('missing explicit texture accepted')
            except ValueError:
                pass
            assert bpy.context.scene == scene and len(bpy.data.scenes) == scene_count
            checks['invalid_texture_input_preserves_active_scene'] = True
        else:
            scene = next(s for s in bpy.data.scenes if review.active(s))
            assert not Path(scene['blade_recon_source_path']).exists()
            assert not Path(scene['blade_recon_texture_source_path']).exists()
            review._tick()
            assert not scene.get('blade_recon_error'), scene.get('blade_recon_error')
            checks['fresh_process_restore_embedded_data_without_external_paths'] = True
        blades = check_images(scene)
        sequence = review.session(scene)['sequence']
        model = original_model()
        rotor = model.Rotor(model.TurbineConfig.from_dict(sequence.data['turbine']))
        for frame in range(1, len(sequence.states)+1):
            scene.frame_set(frame)
            # A visible window evaluates the stale-frame hide driver before the
            # main-thread timer commits geometry. Exercise that cached visibility
            # state explicitly; immediate frame_set -> _tick misses this bug.
            bpy.context.view_layer.update()
            for obj in blades:
                obj.visible_get(view_layer=scene.view_layers[0])
            review._tick()
            bpy.context.view_layer.update()
            expected = rotor.forward(sequence.states[frame-1])[0]
            errors = [float(np.max(np.abs(co(obj)-expected[b].reshape(-1, 3)))) for b,obj in enumerate(blades)]
            assert max(errors) < 1e-4, (frame, errors)
            for b,obj in enumerate(blades):
                assert obj.visible_get(view_layer=scene.view_layers[0]), (frame, obj.name, 'not visible after timer commit')
                assert not obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).hide_viewport, (frame, obj.name, 'evaluated visibility is stale')
                colors = np.empty(len(obj.data.color_attributes['evidence'].data)*4,np.float32)
                obj.data.color_attributes['evidence'].data.foreach_get('color',colors)
                expected_color = np.where(np.repeat(sequence.observed[frame-1,b],sequence.rotor.cfg.n_ring)[:,None],
                                          review.GREEN,review.ORANGE)
                assert np.max(np.abs(colors.reshape(-1,4)-expected_color)) < 1e-6
            for camera in review.session(scene)['cameras']:
                source = next(c for c in sequence.frames[frame-1]['cameras']
                              if c['name'] == camera['blade_recon_camera_name'])
                T = np.eye(4)
                T[:3] = source['T_cv_from_model'][:3]
                assert np.max(np.abs(np.asarray(camera.matrix_world)-np.linalg.inv(T)@np.diag([1,-1,-1,1]))) < 1e-4
                assert abs(camera.data.lens-source['K'][0][0]*36/source['W']) < 1e-4
            if frame in (1,17,18,61,120):
                report['frames'].append({'frame':frame,'max_geometry_error_m':max(errors)})
        checks['all_saved_samples_three_blades_model_geometry_evidence_and_dynamic_cameras'] = True
        # Commit through the timer after large forward/backward seeks. Visibility
        # drivers must follow the new applied-frame property, not retain the
        # hidden state evaluated during frame_set before mesh commit.
        for frame in (1, 61, 17, 18, 17):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            for obj in blades:
                obj.visible_get(view_layer=scene.view_layers[0])
            review._tick()
            bpy.context.view_layer.update()
            assert review.session(scene)['index'] == frame-1
            assert all(obj.visible_get(view_layer=scene.view_layers[0]) and
                       not obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).hide_viewport
                       for obj in blades), ('timer visibility after seek', frame)
        checks['timer_commit_recomputes_visibility_after_seek_and_step'] = True
        scene.frame_set(17)
        review._tick()
        assert bpy.ops.wfrl.blade_recon_step(delta=1) == {'FINISHED'}
        assert scene.frame_current == 18
        assert bpy.ops.wfrl.blade_recon_step(delta=-1) == {'FINISHED'}
        assert scene.frame_current == 17
        scene.wfrl_recon_show_texture = False
        review._tick()
        assert all(ob.data.materials[0].node_tree.nodes['TextureVisibility'].inputs[1].default_value == 0
                   for ob in blades)
        scene.wfrl_recon_show_texture = True
        review._tick()
        checks['backward_seek_single_step_and_texture_evidence_toggle'] = True
        if MODE == 'create':
            # Static legacy results have no imported dynamic-camera transforms.
            # Their graph therefore exposes cached base visibility that a
            # moving camera can accidentally invalidate in a regression fixture.
            static_data = copy.deepcopy(sequence.data)
            for row in static_data['frames']:
                row.pop('cameras', None)
            static_path = fixture/'static-recon.json'
            static_path.write_text(json.dumps(static_data))
            static_scene = review.load(bpy.context, static_path, texture_path=texture)
            static_blades = review.session(static_scene)['blades']
            for frame in (1, 61, 17, 18, 17):
                static_scene.frame_set(frame)
                bpy.context.view_layer.update()
                # Materialize the viewport's cached base visibility before the
                # pending mesh update, as occurs in normal UI timer playback.
                for obj in static_blades:
                    obj.visible_get(view_layer=static_scene.view_layers[0])
                review._tick()
                bpy.context.view_layer.update()
                assert all(obj.visible_get(view_layer=static_scene.view_layers[0]) and
                           not obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).hide_viewport
                           for obj in static_blades), ('static timer visibility', frame)
            bpy.context.window.scene = scene
            review.remove(static_scene)
            checks['static_legacy_cached_viewport_visibility_after_timer_commit'] = True
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'addon-managed.blend'))
            shutil.rmtree(fixture)
            assert not Path(scene['blade_recon_source_path']).exists()
            # Deletion must not destroy a foreign object linked into our scene.
            foreign = bpy.data.objects.new('V02.ForeignLinked', None)
            scene.collection.objects.link(foreign)
            original.collection.objects.link(foreign)
            bpy.context.window.scene = original
            review.remove(scene)
            assert foreign.name in bpy.data.objects and foreign.name in original.objects
            assert sentinel.name in bpy.data.objects and unrelated in bpy.app.handlers.frame_change_pre
            checks['owned_cleanup_preserves_foreign_objects_and_callbacks'] = True
    report['status'] = 'PASS'
    output()
    print('BLADE_RECON_TEXTURE_'+MODE.upper()+'_PASS')
except Exception:
    report['status'] = 'FAIL'
    report['error'] = traceback.format_exc()
    output()
    raise
