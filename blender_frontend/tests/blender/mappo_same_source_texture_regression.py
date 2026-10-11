"""Native data checks for the 601-sample MAPPO same-source texture replay.

Run with Blender --background --factory-startup --python-exit-code 1 --python
this_file. WFRL_TEST_OUTPUT is a new output directory. WFRL_ADDON_MODULE defaults
to source wfrl_blender; set an installed bl_ext module and
WFRL_EXPECTED_ADDON_ROOT to verify an installation. WFRL_SPLIT_INPUT optionally
selects a copied geometry scene. WFRL_SPLIT_SAVE optionally selects a new .blend
inside the output directory. Original recon/calibration are read for independent
provenance checks; no solver, capture, render or canonical-file save is performed.

The visible-window companion imports the checks below without running main().
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
ORIGINAL = ROOT / 'outputs' / 'blade-recon-mappo-two-camera-20261004-a02'
GEOMETRY_SHA256 = '3475e8e43e489076481da2365b3d7179f8667c065ee8dc580486d374c98f616b'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_data(collection, prop, width):
    values = np.empty(len(collection) * width, dtype=np.float32)
    collection.foreach_get(prop, values)
    return values.reshape(-1, width)


def curves(action):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                yield from bag.fcurves


def coordinates(obj, evaluated=True):
    if evaluated:
        obj = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return array_data(obj.data.vertices, 'co', 3)


def save_path(output):
    value = os.environ.get('WFRL_SPLIT_SAVE')
    path = Path(value).expanduser().resolve() if value else output / 'same-source-saved.blend'
    assert path.is_relative_to(output.resolve()), ('save must be additive inside output', path)
    assert not path.exists(), ('refusing to overwrite an existing file', path)
    assert path.suffix == '.blend', path
    return path


class Validation:
    def __init__(self, *, restore_farm=False):
        self.module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
        if self.module == 'wfrl_blender':
            sys.path.insert(0, str(ROOT))
            sys.path.insert(0, str(ROOT / 'blender_frontend'))
        self.addon = importlib.import_module(self.module)
        self.root = Path(self.addon.__file__).resolve().parent
        expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
        if expected:
            assert self.root == Path(expected).expanduser().resolve(), (self.root, expected)
        if self.module.startswith('bl_ext.'):
            assert expected, 'Installed acceptance must specify WFRL_EXPECTED_ADDON_ROOT'
        self.split = importlib.import_module(self.module + '.split_reconstruction')
        self.same = importlib.import_module(self.module + '.split_mappo_texture')
        self.synthetic = importlib.import_module(self.module + '.split_surface_texture')
        self.timing = importlib.import_module(self.module + '.split_reconstruction_timing')
        self.farm = importlib.import_module(self.module + '.farm_flex')
        self.clearance = importlib.import_module(self.module + '.clearance_replay')
        self.scene = bpy.context.scene
        if not hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'):
            self.addon.register()
        if restore_farm and not self.farm.is_active(self.scene):
            self.clearance.on_load(None)
        assert self.farm.is_active(self.scene), self.scene.get('wfrl_clearance_status')
        self.original = json.loads(Path(os.environ.get('WFRL_ORIGINAL_RECON',
            ORIGINAL / 'reconstruction' / 'recon.json')).read_text(encoding='utf-8'))
        self.calibration = json.loads(Path(os.environ.get('WFRL_ORIGINAL_CALIBRATION',
            ORIGINAL / 'input' / 'calibration.json')).read_text(encoding='utf-8'))
        self.value = None

    def geometry_identity(self):
        result = {}
        for bid in (1, 2, 3):
            obj = self.scene.objects[f'SplitRecon.B{bid}']
            keys = obj.data.shape_keys
            assert keys and not keys.use_relative and len(keys.key_blocks) == 601
            assert not keys.animation_data.drivers
            animated = list(curves(keys.animation_data.action))
            assert len(animated) == 1 and animated[0].data_path == 'eval_time'
            assert len(animated[0].keyframe_points) == 601
            assert all(point.interpolation == 'CONSTANT' for point in animated[0].keyframe_points)
            digest = hashlib.sha256()
            for key in keys.key_blocks:
                digest.update(array_data(key.data, 'co', 3).tobytes())
            result[obj.name] = dict(keys=601, vertices=len(obj.data.vertices),
                                   shape_coordinates_sha256=digest.hexdigest(),
                                   source_sha256=obj['split_reconstruction_source_sha256'])
        return result

    def activate(self):
        self.value = self.same.ensure(self.scene)
        assert self.same.active(self.scene) and not self.synthetic.active(self.scene)
        assert self.scene.get('split_texture_current_source') == 'MAPPO'
        assert len(self.value['sequence'].states) == 601
        return self.value

    def provenance(self):
        sequence = self.value['sequence']
        assert (sequence.rotor.cfg.n_sections, sequence.rotor.cfg.n_ring) == (40, 32)
        assert (sequence.rotor.cfg.hub_radius_m, sequence.rotor.cfg.tip_radius_m) == (1.5, 63.)
        assert sequence.fps == 10 and len(sequence.camera_frames) == 601
        assert np.array_equal(sequence.source_frames, np.arange(601))
        assert np.allclose(sequence.times, np.arange(601) / 10, atol=1e-9, rtol=0)
        assert len(self.original['frames']) == len(self.calibration['frames']) == 601
        for index, (original, saved, calibrated) in enumerate(zip(
                self.original['frames'], sequence.frames, self.calibration['frames'])):
            for key in ('state', 'observed_sections', 't', 'sim_t', 'blender_frame', 'T_world_from_model'):
                assert saved[key] == original[key], (index, key, 'original result changed')
            assert original['blender_frame'] == 1 + 6 * index
            assert abs(original['sim_t'] - (117. + index / 10)) < 1e-9
            cameras = sequence.camera_frames[index]
            assert [row['name'] for row in cameras] == ['T1Down', 'NacelleT1']
            assert len(calibrated['cameras']) == 2
            for current, source in zip(cameras, calibrated['cameras']):
                assert current['name'] == source['name']
                assert (current['W'], current['H']) == (source['W'], source['H']) == (960, 540)
                assert np.array_equal(current['K'], source['K'])
                # Original calibration explicitly documents model coordinates,
                # despite the legacy field spelling T_cv_from_world.
                assert np.array_equal(np.asarray(current['T_cv_from_world']),
                                      np.asarray(source['T_cv_from_world'])[:3])
        return dict(samples=601, cameras=['T1Down', 'NacelleT1'], dynamic_calibrations=1202,
                    original_states_times_observations_preserved=True,
                    source_model_coordinates_preserved=True, relative_interval_s=[0, 60],
                    simulation_interval_s=[117, 177], source_fps=10, timeline_fps=60)

    def all_saved_geometry(self):
        """Compare new model/keys to the independently saved original 601 keys."""
        sequence = self.value['sequence']
        maximum = 0.
        legacy = [self.scene.objects[f'SplitRecon.B{i}'] for i in (1, 2, 3)]
        textured = self.same.blades(self.scene)
        assert len(textured) == 3, 'Only the three estimated blades have reconstruction keys'
        for obj in textured:
            keys = obj.data.shape_keys
            assert keys and not keys.use_relative and len(keys.key_blocks) == 601
            assert not keys.animation_data.drivers
            animated = list(curves(keys.animation_data.action))
            assert len(animated) == 1 and animated[0].data_path == 'eval_time'
            assert len(animated[0].keyframe_points) == 601
            assert all(point.interpolation == 'CONSTANT' for point in animated[0].keyframe_points)
        for index in range(601):
            expected = sequence.geometry(index)[0].reshape(3, -1, 3).astype(np.float32)
            for bid, (old, new) in enumerate(zip(legacy, textured)):
                original = array_data(old.data.shape_keys.key_blocks[index].data, 'co', 3)
                actual = array_data(new.data.shape_keys.key_blocks[index].data, 'co', 3)
                error = float(np.max(np.abs(original - expected[bid])))
                maximum = max(maximum, error)
                assert np.array_equal(original, expected[bid]), (index, bid, error, 'forward model differs from original baked geometry')
                assert np.array_equal(actual, original), (index, bid, 'new native texture keys differ')
        return dict(samples=601, blades=3, original_and_new_native_keys_bit_identical=True,
                    original_and_current_model_float32_geometry_bit_identical=True,
                    max_geometry_difference_m=maximum)

    def immutable_texture(self):
        manifest = json.loads(self.same._read(self.scene, 'texture'))
        result = []
        blades = self.same.blades(self.scene)
        assert len(blades) == len(manifest['files']) == 3
        for obj, record in zip(blades, manifest['files']):
            uv = obj.data.uv_layers.get('atlas')
            assert uv and len(uv.data) == len(obj.data.loops)
            image = obj.data.materials[0].node_tree.nodes['SurfaceTexture'].image
            assert image and image.packed_file
            digest = hashlib.sha256(image.packed_file.data).hexdigest()
            assert digest == record['sha256']
            assert list(image.size) == [manifest['atlas']['W'], manifest['atlas']['H']]
            result.append(dict(object=obj.name, vertices_sha256=hashlib.sha256(coordinates(obj, False).tobytes()).hexdigest(),
                               uv_sha256=hashlib.sha256(array_data(uv.data, 'uv', 2).tobytes()).hexdigest(),
                               packed_texture_sha256=digest, image_size=list(image.size)))
        assert len(result) == 3
        return result

    def expected_source_world(self, sim_time):
        preview = self.farm._ACTIVE
        deflection = importlib.import_module(self.module + '.deflection')
        tower = importlib.import_module(self.module + '.tower_motion')
        index = int(np.clip(np.searchsorted(preview.times, sim_time, side='right') - 1,
                            0, len(preview.times) - 2))
        alpha = float(np.clip((sim_time - preview.times[index]) /
                             (preview.times[index + 1] - preview.times[index]), 0, 1))
        lower, upper = preview.deformed_at(index), preview.deformed_at(index + 1)
        pose = preview.poses[index] * (1 - alpha) + preview.poses[index + 1] * alpha
        support = preview.tower_motion
        nacelle = tower.interpolate_transform(support['nacelle'][index], support['nacelle'][index + 1], alpha)
        expected = {}
        for entry, low, high in zip(preview.blades, lower, upper):
            obj, _rest, _world, _index, _weight, turbine, blade, offset = entry
            if not obj.name.startswith('WFRL.Turbine.T1.Blade'):
                continue
            assert low is not None and high is not None
            if preview.reference is not None and 1e-8 < alpha < 1 - 1e-8:
                scalars = preview.comparison.data['scalars']
                h0, _, a0 = deflection.rigid_frame(scalars, preview.poses[index, turbine], blade + 1,
                                                 support['nacelle'][index, turbine])
                h1, _, a1 = deflection.rigid_frame(scalars, preview.poses[index + 1, turbine], blade + 1,
                                                 support['nacelle'][index + 1, turbine])
                hub, _, axes = deflection.rigid_frame(scalars, pose[turbine], blade + 1, nacelle[turbine])
                local_shape = ((low - h0) @ a0) * (1 - alpha) + ((high - h1) @ a1) * alpha
                result = local_shape @ axes.T + hub + offset
            else:
                result = low * (1 - alpha) + high * alpha + offset
            expected[obj.name] = result
        assert len(expected) == 3
        return expected

    def frame(self, frame, *, seek=True):
        """Observe the frame handler's committed output; do not call ensure/apply."""
        scene = self.scene
        if seek:
            scene.frame_set(frame, subframe=0.)
        assert scene.frame_current == frame
        bpy.context.view_layer.update()
        info = self.same.information(scene)
        sample = self.timing.timing_for_frame(frame, self.timing.spec_from_scene(scene))
        assert info and info['index'] == sample.index, (frame, info, sample)
        assert scene[self.same.INDEX] == sample.index
        assert abs(info['time_s'] - sample.index / 10) < 1e-9
        assert abs(info['sample_time_s'] - sample.sample_time_s) < 1e-9
        assert abs(info['timeline_time_s'] - sample.timeline_time_s) < 1e-9
        assert abs(info['age_ms'] - sample.age_ms) < 1e-7
        source = self.clearance.sample(scene)
        assert abs(source['time_s'] - sample.timeline_time_s) < 1e-8
        expected = self.value['sequence'].geometry(sample.index)[0].reshape(3, -1, 3)
        right_errors = []
        native_evaluation = []
        for bid, obj in enumerate(self.same.blades(scene)):
            actual = coordinates(obj)
            error = float(np.max(np.abs(actual - expected[bid])))
            original_obj = scene.objects[f'SplitRecon.B{bid + 1}']
            original = coordinates(original_obj)
            assert np.array_equal(actual, original), (frame, bid, 'evaluated original/texture geometry mismatch')
            right_errors.append(error)
            native_evaluation.append(dict(blade=bid + 1, same_source_eval_time=obj.data.shape_keys.eval_time,
                original_eval_time=original_obj.data.shape_keys.eval_time,
                same_source_key_time=obj.data.shape_keys.key_blocks[sample.index].frame,
                original_key_time=original_obj.data.shape_keys.key_blocks[sample.index].frame,
                model_to_original_native_difference_m=float(np.max(np.abs(original - expected[bid])))))
        expected_source = self.expected_source_world(sample.timeline_time_s)
        left_errors = []
        for name, expected_world in expected_source.items():
            obj = scene.objects[name]
            matrix = np.asarray(obj.matrix_world, dtype=np.float64)
            actual = coordinates(obj, False) @ matrix[:3, :3].T + matrix[:3, 3]
            error = float(np.max(np.abs(actual - expected_world)))
            assert error < 2e-4, (frame, name, error)
            left_errors.append(error)
        return dict(frame=frame, index=sample.index, relative_time_s=info['time_s'],
                    timeline_time_s=sample.timeline_time_s, held_sample_time_s=sample.sample_time_s,
                    age_ms=sample.age_ms, original_and_texture_evaluated_geometry_bit_identical=True,
                    max_saved_model_to_native_evaluation_difference_m=max(right_errors),
                    native_evaluation=native_evaluation,
                    max_left_bound_package_error_m=max(left_errors))

    def motion_signature(self):
        left = []
        for bid in (1, 2, 3):
            obj = self.scene.objects[f'WFRL.Turbine.T1.Blade{bid}']
            matrix = np.asarray(obj.matrix_world, dtype=np.float64)
            world = coordinates(obj, False) @ matrix[:3, :3].T + matrix[:3, 3]
            left.append(hashlib.sha256(world.tobytes()).hexdigest())
        return {
            'left': left,
            'right': [hashlib.sha256(coordinates(obj).tobytes()).hexdigest() for obj in self.same.blades(self.scene)],
            'frame': self.scene.frame_current, 'index': self.scene[self.same.INDEX],
        }


def main():
    output = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(status='RUNNING', scope='NATIVE_BACKGROUND_DATA_AND_SAVE_REOPEN; NO_VISIBLE_WINDOW_OR_FPS_CLAIM')
    try:
        assert bpy.app.background
        module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
        if module == 'wfrl_blender':
            sys.path.insert(0, str(ROOT))
            sys.path.insert(0, str(ROOT / 'blender_frontend'))
        addon = importlib.import_module(module)
        canonical = Path(addon.__file__).resolve().parent / 'assets/examples/mappo_reconstruction_split.blend'
        assert sha256(canonical) == os.environ.get('WFRL_EXPECTED_GEOMETRY_SHA256', GEOMETRY_SHA256)
        opened = Path(os.environ.get('WFRL_SPLIT_INPUT', canonical)).expanduser().resolve()
        bpy.ops.wm.open_mainfile(filepath=str(opened), load_ui=False, use_scripts=False)
        validation = Validation(restore_farm=True)
        scene = validation.scene
        assert not validation.same.active(scene) and not validation.synthetic.active(scene)
        original_identity = validation.geometry_identity()
        scene.frame_set(52)
        validation.activate()
        report.update(addon_module=module, addon_file=validation.addon.__file__, blender=bpy.app.version_string,
                      opened_blend=str(opened), canonical_geometry_sha256=sha256(canonical),
                      provenance=validation.provenance(), saved_geometry=validation.all_saved_geometry())
        texture = validation.immutable_texture()
        report['frames'] = [validation.frame(frame) for frame in (1, 6, 7, 49, 52, 54, 55, 901, 1801, 2701, 3598, 3601, 52)]
        report['existing_native_evaluation_baseline'] = dict(
            maximum_difference_m_among_tested_frames=max(
                row['max_saved_model_to_native_evaluation_difference_m'] for row in report['frames']),
            original_and_new_native_evaluation_bit_identical=True,
            interpretation='Original absolute-key floating-point time accumulation and native evaluation; saved sample coordinates remain bit-identical to float32 forward-model output',
            original_model_precision_acceptance=False, original_data_or_key_times_modified=False,
            original_failure_evidence='source-native/attempt02/native-validation.json; source-native/evaluation-probe/probe2.json')
        before = validation.motion_signature()
        mode = scene.wfrl_recon_texture_mode
        validation.same.toggle(scene)
        validation.same.toggle(scene)
        assert scene.wfrl_recon_texture_mode == mode
        assert validation.motion_signature() == before and validation.immutable_texture() == texture
        for start, direction, expected in ((52, 1, 55), (52, -1, 49), (1, -1, 1), (3601, 1, 3601)):
            scene.frame_set(start)
            validation.same.step(scene, direction)
            assert scene.frame_current == expected
            validation.frame(expected, seek=False)
        assert validation.geometry_identity() == original_identity
        # An independent SYNTH cursor must still have its original 120 samples.
        separate = bpy.data.scenes.new('Independent synthetic identity check')
        synth = validation.synthetic.ensure(separate)
        assert len(synth['sequence'].states) == 120
        assert 'SYNTHETIC' in synth['sequence'].source_label.upper()
        assert validation.synthetic.active(separate) and not validation.same.active(separate)
        assert validation.same.camera(scene) != validation.synthetic.camera(separate)
        separate_name = separate.name
        scene.frame_set(52)
        saved = save_path(output)
        bpy.ops.wm.save_as_mainfile(filepath=str(saved))
        bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False)
        reopened = Validation(restore_farm=True)
        reopened.activate()
        assert reopened.scene.frame_current == 52
        assert reopened.geometry_identity() == original_identity
        assert reopened.immutable_texture() == texture
        reopened.frame(52, seek=False)
        assert len(reopened.synthetic.ensure(bpy.data.scenes[separate_name])['sequence'].states) == 120
        report.update(status='PASS', immutable_texture=texture, sample_steps_and_endpoint_clamps=True,
                      original_geometry_identity_preserved=True, independent_synthetic_identity_preserved=True,
                      saved=str(saved), save_reopen_restored=True,
                      software_sync_acceptance_only=True, reconstruction_accuracy_accepted=False)
    except Exception:
        report.update(status='FAIL', error=traceback.format_exc())
        (output / 'native-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    (output / 'native-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('MAPPO_SAME_SOURCE_TEXTURE_NATIVE_PASS', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
