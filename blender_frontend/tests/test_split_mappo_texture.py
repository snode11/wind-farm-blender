"""Same-source identity and committed clock checks without a Blender window."""
import copy
import hashlib
import json
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np

from wfrl_blender import split_mappo_texture as texture
from wfrl_blender.blade_recon_data import ReconSequence


class SameSourceTextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provenance = json.loads((texture.package_path()/'manifest.json').read_text())
        cls.sequence = ReconSequence(texture.package_path()/'recon.json')

    def test_delivered_clock_preserves_601_actual_sample_ids(self):
        spec = texture._validate_clock(self.sequence)
        self.assertEqual(spec.frame_end, 3601)
        self.assertEqual(spec.sampling_hz, 10.)
        sequence = copy.copy(self.sequence)
        sequence.times = self.sequence.times.copy()
        sequence.times[9] += .001
        with self.assertRaisesRegex(ValueError, '601'):
            texture._validate_clock(sequence)
        sequence.times = self.sequence.times
        sequence.source_frames = np.roll(self.sequence.source_frames, 1)
        with self.assertRaisesRegex(ValueError, '601'):
            texture._validate_clock(sequence)

    def test_missing_source_identity_is_rejected_instead_of_defaulted(self):
        scene = {'wfrl_farm_manifest_sha256': texture.SOURCE_HASH}
        texture._validate_source(scene, self.provenance, check_files=False)
        bad = copy.deepcopy(self.provenance)
        bad.pop('mappo_manifest_sha256', None)
        bad['source_package_hashes'].pop('assets/mappo/manifest.json')
        with self.assertRaisesRegex(ValueError, '不匹配'):
            texture._validate_source(scene, bad, check_files=False)
        with self.assertRaisesRegex(ValueError, '不匹配'):
            texture._validate_source({'wfrl_farm_manifest_sha256': 'another clock-compatible source'},
                                     self.provenance, check_files=False)
        bad = copy.deepcopy(self.provenance)
        bad['clock']['simulation_start_s'] = 0.
        with self.assertRaisesRegex(ValueError, '时间映射'):
            texture._validate_source(scene, bad, check_files=False)

    def test_runtime_model_identity_is_required_for_saved_and_fresh_provenance(self):
        scene = {'wfrl_farm_manifest_sha256': texture.SOURCE_HASH}
        for kind in ('missing_model', 'missing_hash', 'empty_hash', 'wrong_hash'):
            bad = copy.deepcopy(self.provenance)
            if kind == 'missing_model':
                bad.pop('model')
            elif kind == 'missing_hash':
                bad['model'].pop('sha256')
            else:
                bad['model']['sha256'] = '' if kind == 'empty_hash' else '0'*64
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    texture._validate_source(scene, bad, check_files=False)

    def test_runtime_requires_complete_declared_absolute_clock(self):
        scene = {'wfrl_farm_manifest_sha256': texture.SOURCE_HASH}
        fields = ('samples', 'fps', 'source_frame_start', 'source_frame_end',
                  'time_start_s', 'time_end_s', 'simulation_start_s', 'simulation_end_s',
                  'source_hz', 'timeline_fps', 'stride', 'frame_start', 'frame_end')
        for field in fields:
            for kind in ('missing', 'wrong', 'string', 'boolean'):
                bad = copy.deepcopy(self.provenance)
                clock = bad['clock']
                if kind == 'missing':
                    clock.pop(field)
                elif kind == 'wrong':
                    clock[field] += 1
                elif kind == 'string':
                    clock[field] = str(clock[field])
                else:
                    clock[field] = bool(clock[field])
                with self.subTest(field=field, kind=kind):
                    with self.assertRaisesRegex(ValueError, '时间映射'):
                        texture._validate_source(scene, bad, check_files=False)

    def test_runtime_absolute_sample_clock_requires_saved_values(self):
        for field in ('sim_t', 'blender_frame'):
            for kind in ('missing', 'wrong', 'nonfinite', 'string', 'wrong_numeric_type'):
                sequence = copy.copy(self.sequence)
                sequence.frames = copy.deepcopy(self.sequence.frames)
                row = sequence.frames[13]
                if kind == 'missing':
                    row.pop(field)
                elif kind == 'wrong':
                    row[field] += 1
                elif kind == 'nonfinite':
                    row[field] = float('nan')
                elif kind == 'string':
                    row[field] = str(row[field])
                elif field == 'blender_frame':
                    row[field] = float(row[field])
                else:
                    row[field] = None
                with self.subTest(field=field, kind=kind):
                    with self.assertRaises(ValueError):
                        texture._validate_clock(sequence)

    def test_committed_information_reports_timeline_and_held_sample_separately(self):
        scene = NS(as_pointer=lambda: 900, frame_current=52, frame_subframe=0.)
        try:
            texture._SESSIONS[900] = dict(sequence=self.sequence, index=8)
            value = texture.information(scene)
            self.assertEqual(value['index'], 8)
            self.assertEqual(value['time_s'], .8)
            self.assertEqual(value['sample_time_s'], 117.8)
            self.assertAlmostEqual(value['timeline_time_s'], 117.85)
            self.assertEqual(value['age_ms'], 50.)
            self.assertTrue(value['synchronized'])
            scene.frame_current = 3602
            self.assertFalse(texture.information(scene)['in_range'])
            self.assertIsNone(texture.information(scene)['age_ms'])
        finally:
            texture._SESSIONS.pop(900, None)

    def test_separate_markers_keep_independent_synthetic_identity(self):
        from wfrl_blender import split_reconstruction as split, split_surface_texture as synthetic
        scene = {texture.MARKER: True, synthetic.MARKER: True, 'split_texture_current_source': 'MAPPO'}
        self.assertTrue(texture.active(scene))
        self.assertFalse(synthetic.active(scene))
        self.assertIs(split.texture_adapter(scene), texture)
        scene['split_texture_current_source'] = 'SYNTHETIC'
        self.assertFalse(texture.active(scene))
        self.assertTrue(synthetic.active(scene))
        self.assertIs(split.texture_adapter(scene), synthetic)

    def test_manual_navigation_releases_surface_tracking_without_changing_view(self):
        class Scene(dict):
            def as_pointer(self):
                return 901
        original = dict(perspective='ORTHO', location=(10001., 2., 3.),
                        rotation=(1., 0., 0., 0.), distance=2.7)
        for change in (dict(distance=180.), dict(location=(10005., 2., 3.)),
                       dict(rotation=(.7, .7, 0., 0.)), dict(perspective='PERSP')):
            edited = dict(original, **change)
            rv = NS(view_perspective=edited['perspective'], view_location=edited['location'],
                    view_rotation=edited['rotation'], view_distance=edited['distance'])
            area = NS(as_pointer=lambda: 902, spaces=NS(active=NS(region_3d=rv)))
            for restored in (False, True):
                scene = Scene({texture.DETAIL: True})
                value = dict(tracked_views={902: original} if not restored else {})
                if restored:
                    scene[texture.TRACKED_VIEW] = original
                with self.subTest(change=change, restored=restored):
                    texture.track(scene, area, value=value)
                    self.assertFalse(scene[texture.DETAIL])
                    self.assertEqual(texture._tracking_view(area), edited)
                    self.assertFalse(value['tracked_views'])

    def test_surface_tracking_tolerates_native_float_roundoff(self):
        original = dict(perspective='ORTHO', location=(10001., 2., 3.),
                        rotation=(1., 0., 0., 0.), distance=2.7)
        self.assertTrue(texture._same_tracking_view(original, dict(original, distance=2.70000001)))


class CompleteTurbineDisplayTests(unittest.TestCase):
    class Matrix:
        def __init__(self, translation):
            self.translation = tuple(translation)

        def copy(self):
            return type(self)(self.translation)

    class Mesh:
        def copy(self):
            return type(self)()

    class Object(dict):
        def __init__(self, name, kind='MESH', translation=texture.REFERENCE_OFFSET):
            super().__init__()
            self.name, self.type = name, kind
            self.matrix_world = CompleteTurbineDisplayTests.Matrix(translation)
            self.data = CompleteTurbineDisplayTests.Mesh()
            self.parent = None

        def copy(self):
            obj = type(self)(self.name, self.type, self.matrix_world.translation)
            obj.data = self.data
            obj.update(self)
            return obj

        def animation_data_clear(self):
            self.animation_cleared = True

        def hide_set(self, hidden):
            self.hidden = hidden

    class Objects(dict):
        def __iter__(self):
            return iter(self.values())

    class Scene(dict):
        def as_pointer(self):
            return id(self)

    def scene(self):
        scene = self.Scene()
        scene.objects = self.Objects()
        for part in texture.STRUCTURE:
            name = 'SplitRecon.Reference.' + part
            scene.objects[name] = self.Object(name)
        scene.objects['SplitRecon.B1'] = self.Object('SplitRecon.B1')
        for bid in (1, 2, 3):
            name = texture.PREFIX + f'B{bid}'
            scene.objects[name] = self.Object(name, translation=texture.OFFSET)
        scene.collection = NS(objects=NS(link=lambda obj: scene.objects.__setitem__(obj.name, obj)))
        return scene

    def test_context_delegates_to_shared_source_model_without_touching_estimates(self):
        from wfrl_blender import split_turbine_context as context
        scene = self.scene()
        estimates = texture.blades(scene)
        meshes = [obj.data for obj in estimates]
        # A complete source turbine has more than the old three placeholders.
        structure = [self.Object(texture.PREFIX + 'Reference.' + part)
                     for part in ('Tower', 'Nacelle', 'Hub', 'Spinner', 'YawSeal')]
        with patch.object(context, 'ensure', return_value=structure) as ensure:
            self.assertEqual(texture._ensure_structure(scene), structure)
        ensure.assert_called_once_with(
            scene, prefix=texture.PREFIX + 'Reference.', offset=texture.OFFSET,
            names_key='split_mappo_texture_structure_names')
        self.assertEqual(texture.blades(scene), estimates)
        self.assertEqual([obj.data for obj in estimates], meshes)

    def test_display_inventory_includes_all_context_meshes_and_three_estimates(self):
        scene = self.scene()
        structure = [self.Object(texture.PREFIX + 'Reference.' + part)
                     for part in ('Tower', 'Nacelle', 'Hub', 'Spinner', 'YawSeal')]
        for obj in structure:
            scene.objects[obj.name] = obj
        scene['split_mappo_texture_structure_names'] = [obj.name for obj in structure]
        self.assertEqual(texture.objects(scene), texture.blades(scene) + structure)
        self.assertEqual(len(texture.blades(scene)), 3)
        self.assertEqual(len(texture.owned(scene)), 8)

    def test_saved_context_validation_errors_are_not_hidden(self):
        from wfrl_blender import split_turbine_context as context
        for reason in ('missing component', 'duplicate component', 'wrong classification'):
            scene = self.scene()
            with self.subTest(reason=reason), \
                    patch.object(context, 'ensure', side_effect=ValueError(reason)):
                with self.assertRaisesRegex(ValueError, reason):
                    texture._ensure_structure(scene)

    def test_camera_copies_canonical_transform_and_data_without_editing_source(self):
        scene = self.scene()
        original = self.Object('SplitRecon.Camera', 'CAMERA', (360., 10000., 90.))
        scene.objects[original.name] = original
        camera = texture._create_camera(scene, NS(), np.zeros(3), 148.05)
        self.assertEqual(camera.matrix_world.translation, (10360., 0., 90.))
        self.assertEqual(original.matrix_world.translation, (360., 10000., 90.))
        self.assertIsNot(camera.data, original.data)
        self.assertTrue(camera['split_mappo_texture_owned'])

    def test_legacy_tracking_never_moves_the_view_even_when_forced(self):
        scene = self.scene()
        scene[texture.DETAIL] = True
        scene[texture.TRACKED_VIEW] = dict(distance=2.)
        value = dict(tracked_views={1: dict(distance=2.)})
        with patch.object(texture, '_tracked_surface', side_effect=AssertionError('automatic focus')):
            texture.track(scene, object(), value=value, force=True)
        self.assertFalse(scene[texture.DETAIL])
        self.assertNotIn(texture.TRACKED_VIEW, scene)
        self.assertEqual(value['tracked_views'], {})

    def test_preset_positions_once_and_leaves_navigation_owned_by_user(self):
        scene = self.scene()
        scene[texture.DETAIL] = True
        value = dict(tracked_views={1: dict(distance=2.)})
        context, area = NS(scene=scene), NS(spaces=NS(active=NS(show_region_ui=True)))
        with patch.object(texture, 'ensure', return_value=value), \
                patch.object(texture, '_focus_surface', return_value='positioned') as focus:
            self.assertEqual(texture.focus(context, area, 'TIP'), 'positioned')
        focus.assert_called_once_with(scene, area, value)
        self.assertEqual(scene.wfrl_recon_inspect_blade, '3')
        self.assertFalse(scene[texture.DETAIL])
        self.assertEqual(value['tracked_views'], {})

    def test_frame_callback_commits_geometry_without_focusing(self):
        scene = self.scene()
        scene[texture.MARKER] = True
        scene['split_texture_current_source'] = 'MAPPO'
        scene[texture.DETAIL] = True  # Older saved files may still carry this.
        value = dict(index=0)
        texture._SESSIONS[scene.as_pointer()] = value
        try:
            with patch.object(texture, 'apply') as apply, \
                    patch.object(texture, '_runtime', return_value=value), \
                    patch.object(texture, 'track', side_effect=AssertionError('automatic focus')):
                texture.frame_changed(scene)
            apply.assert_called_once_with(scene, value=value)
        finally:
            texture._SESSIONS.pop(scene.as_pointer(), None)


class CachedTextureRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SameSourceTextureTests.setUpClass.__func__(cls)

    class ID(NS):
        def as_pointer(self):
            return id(self)

    class Scene(dict):
        def as_pointer(self):
            return id(self)

    def setUp(self):
        self.scene = self.Scene(wfrl_farm_manifest_sha256=texture.SOURCE_HASH,
            wfrl_farm_flex_path='__WFRL_BUNDLED_MAPPO__', split_mappo_texture_review=True,
            split_texture_current_source='MAPPO', split_mappo_texture_camera_name=texture.CAMERA,
            split_mappo_texture_model_sha256=self.sequence.model_sha256)
        self.scene.objects = {}
        names = []
        for bid in (1, 2, 3):
            name = texture.PREFIX + f'B{bid}'
            names.append(name)
            mesh = self.ID(shape_keys=self.ID(key_blocks=[None]*601), vertices=[None]*(40*32))
            self.scene.objects[name] = self.ID(name=name, type='MESH', data=mesh)
        self.scene['split_mappo_texture_blade_names'] = names
        self.scene.objects[texture.CAMERA] = self.ID(name=texture.CAMERA, type='CAMERA')
        self.blocks = {}
        for key in ('recon', 'provenance', 'texture', 'texture_source'):
            name = texture.PREFIX + key
            raw = json.dumps(self.provenance) if key == 'provenance' else '{}'
            block = self.ID(raw=raw)
            block.as_string = lambda block=block: block.raw
            self.blocks[name] = block
            self.scene['split_mappo_texture_' + key + '_text'] = name
            self.scene['split_mappo_texture_' + key + '_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
        self.bpy = patch.dict(sys.modules, {'bpy': NS(data=NS(texts=self.blocks))})
        self.bpy.start()
        source_root = texture.package_path().parent / 'mappo'
        self.value = dict(sequence=self.sequence, blades=texture.blades(self.scene), structure=[],
            index=8, provenance=self.provenance, binding=texture._runtime_signature(self.scene),
            source_root=source_root, source_signature=texture._source_signature(source_root))
        texture._SESSIONS[self.scene.as_pointer()] = self.value

    def tearDown(self):
        texture._SESSIONS.pop(self.scene.as_pointer(), None)
        self.bpy.stop()

    def test_stable_poll_reuses_runtime_without_restore_json_or_activation(self):
        with patch.object(texture, '_restore', side_effect=AssertionError('restore')), \
                patch.object(texture, '_activate', side_effect=AssertionError('activate')), \
                patch.object(texture.json, 'loads', side_effect=AssertionError('JSON parse')), \
                patch.object(texture, '_ensure_structure', return_value=[]) as bind, \
                patch.object(texture, 'apply', return_value=self.value) as apply:
            for _ in range(20):
                self.assertIs(texture.refresh(self.scene), self.value)
        self.assertEqual(apply.call_count, 20)
        self.assertTrue(all(call.kwargs == {'cached': True} for call in bind.call_args_list))

    def test_mesh_keys_text_or_source_changes_require_full_restore(self):
        for change in ('mesh', 'keys', 'sample_count', 'saved_hash', 'source_path', 'text_id'):
            original = texture._runtime_signature(self.scene)
            name = self.scene['split_mappo_texture_blade_names'][0]
            blade = self.scene.objects[name]
            old_mesh, old_keys = blade.data, blade.data.shape_keys
            saved = dict(self.scene)
            text_name = self.scene['split_mappo_texture_provenance_text']
            old_text = self.blocks[text_name]
            if change == 'mesh':
                blade.data = self.ID(shape_keys=old_keys, vertices=old_mesh.vertices)
            elif change == 'keys':
                blade.data.shape_keys = self.ID(key_blocks=[None]*601)
            elif change == 'sample_count':
                old_keys.key_blocks.pop()
            elif change == 'saved_hash':
                self.scene['split_mappo_texture_model_sha256'] = 'wrong model'
            elif change == 'source_path':
                self.scene['wfrl_farm_flex_path'] = 'unrelated source'
            else:
                self.blocks[text_name] = self.ID(as_string=old_text.as_string)
            try:
                with self.subTest(change=change), patch.object(texture, '_restore',
                        side_effect=ValueError('complete saved validation rejected fixture')) as restore:
                    with self.assertRaisesRegex(ValueError, 'complete saved validation'):
                        texture._runtime(self.scene)
                    restore.assert_called_once_with(self.scene)
            finally:
                blade.data = old_mesh
                old_mesh.shape_keys = old_keys
                old_keys.key_blocks = [None]*601
                self.blocks[text_name] = old_text
                self.scene.clear()
                self.scene.update(saved)
                self.assertEqual(texture._runtime_signature(self.scene), original)

    def test_deleted_blade_and_mutated_embedded_source_are_reported(self):
        name = self.scene['split_mappo_texture_blade_names'][0]
        blade = self.scene.objects.pop(name)
        with self.assertRaisesRegex(ValueError, '网格或相机缺失'):
            texture._runtime(self.scene)
        self.scene.objects[name] = blade
        self.blocks[self.scene['split_mappo_texture_provenance_text']].raw = '{}'
        with patch.object(texture, '_ensure_structure', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'provenance 数据校验失败'):
                texture.refresh(self.scene)

    def test_changed_source_file_metadata_runs_complete_hash_validation(self):
        with patch.object(texture, '_ensure_structure', return_value=[]), \
                patch.object(texture, '_source_signature', return_value=('changed',)), \
                patch.object(texture, '_validate_source', wraps=texture._validate_source) as validate, \
                patch.object(texture, 'apply', return_value=self.value):
            texture.refresh(self.scene)
        self.assertTrue(any(call.kwargs.get('check_files') is not False for call in validate.call_args_list))

    def test_runtime_source_model_and_clock_validation_remains_mandatory(self):
        invalid = copy.deepcopy(self.provenance)
        invalid['model']['sha256'] = 'wrong model'
        self.value['provenance'] = invalid
        with self.assertRaisesRegex(ValueError, '前向模型'):
            texture._runtime(self.scene)

    def test_lifecycle_discards_runtime_and_uses_full_saved_restore(self):
        texture.clear_runtime()
        restored = dict(self.value, index=None)
        with patch.object(texture, '_restore', return_value=restored) as restore, \
                patch.object(texture, '_ensure_structure', return_value=[]):
            self.assertIs(texture._runtime(self.scene), restored)
        restore.assert_called_once_with(self.scene)


if __name__ == '__main__':
    unittest.main()
