"""Physics checks against recorded outputs and saved source surfaces."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
from wfrl_blender.deflection import rigid_frame, compare, read_comparison


class DeflectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = ROOT / 'blender_frontend/wfrl_blender/assets/mappo'
        cls.geometry = dict(np.load(cls.package / 'geometry.npz'))
        cls.manifest = json.loads((cls.package / 'manifest.json').read_text())
        cls.data = read_comparison(cls.package, cls.manifest, cls.geometry['times'], cls.geometry['poses'])

    def test_all_saved_frames_and_blades(self):
        errors = []
        d, g = self.data, self.geometry
        for b in (1, 2, 3):
            hub, axes, pitched = rigid_frame(d['scalars'], [0] * 6, b)
            rest = hub + pitched @ d['tip_local_m'] if 'tip_local_m' in d else hub + axes[:, 2] * d['scalars']['TipRad']
            for i, pose in enumerate(d['poses']):
                support = d['nacelle'][i] if 'nacelle' in d else None
                hub, axes, pitched = rigid_frame(d['scalars'], pose, b, support)
                if 'tip_local_m' in d:
                    axes = pitched
                    reference = hub + axes @ d['tip_local_m']
                else:
                    reference = hub + axes[:, 2] * d['scalars']['TipRad']
                tr = g['transforms'][i, 0, b - 1, -1]
                actual = tr[:, :3] @ rest + tr[:, 3]
                row = compare(actual, reference, axes, d['simulation'][i, b - 1])
                errors.append(row['error'])
                self.assertAlmostEqual(np.linalg.norm(row['components']), row['distance'], places=9)
                if i == 0 and b == 1 and 'nacelle' not in d:
                    np.testing.assert_allclose(row['components'][:2], [3.47831678, -.84240049], atol=.00001)
                    self.assertGreater(row['distance'], abs(row['components'][0]))
        maximum = np.max(np.abs(errors), axis=0)
        np.testing.assert_array_less(maximum, [.005] * 3 if 'tip_local_m' in d else ([.0002, .0002] if 'nacelle' in d else [.00001, .00006]))
        print('ALL_7203_SAVED_TIPS_MAX_ERROR_M', maximum.tolist())

    def test_deflection_channels_cannot_move_either_point(self):
        s = self.data['scalars']
        hub, axes, pitched = rigid_frame(s, self.data['poses'][0], 1)
        reference = hub + axes[:, 2] * s['TipRad']
        actual = reference + axes @ np.array([3., -1., -.2])
        a = compare(actual, reference, axes, [3, -1])
        b = compare(actual, reference, axes, [103, 99])
        np.testing.assert_array_equal(a['actual'], b['actual'])
        np.testing.assert_array_equal(a['reference'], b['reference'])
        np.testing.assert_array_equal(a['components'], b['components'])
        np.testing.assert_allclose(b['error'] - a['error'], [-100, -100])

    def test_rigid_motion_and_pitch_basis(self):
        s = self.data['scalars']
        for azimuth in (0, 90, 180, 721):
            pose = [25, azimuth, 11, 30, 40, 50]
            for blade in (1, 2, 3):
                hub, axes, pitched = rigid_frame(s, pose, blade)
                np.testing.assert_allclose(axes.T @ axes, np.eye(3), atol=1e-12)
                np.testing.assert_allclose(axes[:, 2], pitched[:, 2], atol=1e-12)
                ref = hub + axes[:, 2] * s['TipRad']
                zero = compare(ref, ref, axes, [0, 0])
                np.testing.assert_array_equal(zero['components'], [0, 0, 0])

    def test_common_support_motion_does_not_become_blade_deflection(self):
        from wfrl_blender.deflection import rotation
        s=self.data['scalars'];pose=[23,181,11,4,5,6]
        support=rotation(0,.4)@rotation(1,.7)@rotation(2,pose[0])
        translation=np.array([.4,-.1,87.599])-support@np.array([0,0,87.6])
        nacelle=np.column_stack([support,translation])
        for blade in (1,2,3):
            local_pose=[0,*pose[1:]]
            hub0,axes0,_=rigid_frame(s,local_pose,blade)
            actual=support@(hub0+axes0[:,2]*s['TipRad'])+translation
            hub,axes,_=rigid_frame(s,pose,blade,nacelle)
            reference=hub+axes[:,2]*s['TipRad']
            np.testing.assert_allclose(compare(actual,reference,axes,[0,0])['components'],0,atol=1e-12)

    def test_corruption_and_missing(self):
        manifest = copy.deepcopy(self.manifest)
        del manifest['files']['deflection-t1.json']
        self.assertIsNone(read_comparison(self.package, manifest, self.geometry['times'], self.geometry['poses']))
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'deflection-t1.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                read_comparison(temp, self.manifest, self.geometry['times'], self.geometry['poses'])

    def test_retained_vtp_independent_transport(self):
        from wfrl.lidar.physics import read_surface
        from wfrl.blender_bridge.blade_flex_export import fit_sections
        import hashlib
        source_run = self.package / 'source-run.json'
        case = (json.loads(source_run.read_text())['case_dir'] if source_run.exists()
                else '__simul__/fastfarm/FastFarm__208s__3T_1789489620.102902')
        farm = ROOT / case / 'FarmInputs'
        if not (farm / 'vtk/Case.T1.Blade1Surface.00000.vtp').exists():
            self.skipTest('Original VTP is not distributed with the offline release')
        hashes = json.loads((self.package / 'source-surfaces.json').read_text())
        for b in (1, 2, 3):
            def surface(frame):
                key = f'vtk/Case.T1.Blade{b}Surface.{frame:05d}.vtp'
                raw = (farm / key).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), hashes[key])
                return read_surface(farm / key)[0].reshape(19, -1, 3)
            initial = (np.load(self.package / 'reference-surfaces.npz')['blades'][b - 1].reshape(19, -1, 3)
                       if 'tip_local_m' in self.data else surface(0))
            hub, axes, pitched = rigid_frame(self.data['scalars'], [0] * 6, b)
            rest = (hub + pitched @ self.data['tip_local_m'] if 'tip_local_m' in self.data
                    else hub + axes[:, 2] * self.data['scalars']['TipRad'])
            for i in (0, 1200, 2400):
                r, t, error = fit_sections(initial, surface(round(self.geometry['times'][i] * 40)))
                self.assertLess(error, .002)
                original = r[-1] @ rest + t[-1]
                tr = self.geometry['transforms'][i, 0, b - 1, -1]
                np.testing.assert_allclose(tr[:, :3] @ rest + tr[:, 3], original, atol=.00002, rtol=0)


if __name__ == '__main__':
    unittest.main()
