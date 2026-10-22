"""Regression checks for source-profile landmarks and a closed blade loft."""
import math
import unittest
from collections import Counter
from wfrl_blender.turbine_geometry import _resample_ring, blade_mesh, geometry_data


class BladeSectionTests(unittest.TestCase):
    def test_source_landmarks_and_surface_order(self):
        for airfoil in geometry_data()['airfoils']:
            source=airfoil['coordinates']
            ring=_resample_ring(source,96)
            self.assertEqual(ring[48],tuple(min(source,key=lambda p:p[0])))
            self.assertEqual(ring[0][0],max(p[0] for p in source))
            self.assertEqual(len(set(ring)),96)
            for i in range(1,48):
                self.assertAlmostEqual(ring[i][0],ring[-i][0],places=12)
                self.assertGreater(ring[i][1],ring[-i][1])
            area=sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(ring,ring[1:]+ring[:1]))/2
            self.assertGreater(area,0)

    def test_loft_is_closed_and_has_no_zero_length_edges(self):
        vertices,faces=blade_mesh()
        edges=Counter()
        for face in faces:
            for a,b in zip(face,face[1:]+face[:1]):
                self.assertGreater(math.dist(vertices[a],vertices[b]),1e-8)
                edges[tuple(sorted((a,b)))]+=1
        self.assertTrue(all(count==2 for count in edges.values()))
        self.assertAlmostEqual(max(v[2] for v in vertices),62.9999)
        self.assertEqual(min(v[2] for v in vertices),1.5)

    def test_rejects_odd_sampling_count(self):
        with self.assertRaises(ValueError):
            _resample_ring(geometry_data()['airfoils'][0]['coordinates'],95)


class SmoothPresentationTests(unittest.TestCase):
    def test_source_sections_preserved_before_tip_finish(self):
        data = geometry_data()
        vertices, _ = blade_mesh()
        for i, (span, curve, sweep, _, twist, chord, afid) in enumerate(data['blade_stations'][:-1]):
            profile = data['airfoils'][int(afid)-1]
            beta = math.radians(twist)
            for j, (x, y) in enumerate(_resample_ring(profile['coordinates'], 96)):
                u, v = (x-profile['reference'][0])*chord, (y-profile['reference'][1])*chord
                expected = (curve-u*math.sin(beta)+v*math.cos(beta),
                            sweep+u*math.cos(beta)+v*math.sin(beta), span+1.5)
                self.assertLess(math.dist(vertices[i*8*96+j], expected), 1e-10)

    def test_tip_contracts_to_one_vertex_without_flat_cap(self):
        vertices, faces = blade_mesh()
        tip = vertices[-1]
        self.assertEqual(sum(abs(p[2]-tip[2]) < 1e-9 for p in vertices), 1)
        widths = []
        for i in range(17*8, 17*8+24):
            ring = vertices[i*96:(i+1)*96]
            widths.append(max(p[1] for p in ring)-min(p[1] for p in ring))
        self.assertTrue(all(a > b > 0 for a, b in zip(widths, widths[1:])))
        self.assertTrue(all(len(face) == 3 for face in faces if len(vertices)-1 in face))

    def test_interpolation_has_shared_slopes_and_no_overshoot(self):
        from wfrl_blender.turbine_geometry import _smooth_slopes, _hermite
        xs, ys = [0., 1., 4., 5.], [0., 2., 2.5, 1.]
        slopes = _smooth_slopes(xs, ys)
        for i in range(3):
            h = xs[i+1]-xs[i]
            for k in range(101):
                value = _hermite(ys[i], ys[i+1], slopes[i], slopes[i+1], h, k/100)
                self.assertTrue(min(ys[i:i+2])-1e-12 <= value <= max(ys[i:i+2])+1e-12)
        for i in (1, 2):
            eps = 1e-6
            left_h, right_h = xs[i]-xs[i-1], xs[i+1]-xs[i]
            left = (ys[i]-_hermite(ys[i-1], ys[i], slopes[i-1], slopes[i], left_h, 1-eps/left_h))/eps
            right = (_hermite(ys[i], ys[i+1], slopes[i], slopes[i+1], right_h, eps/right_h)-ys[i])/eps
            self.assertAlmostEqual(left, right, places=4)


    def test_tip_has_rounded_pole_instead_of_a_cusp(self):
        vertices, _ = blade_mesh()
        tip = vertices[-1]
        ratios = []
        for offset in (1, 2, 3):
            ring = vertices[-1-offset*96:-1-(offset-1)*96]
            radius_squared = sum((p[0]-tip[0])**2+(p[1]-tip[1])**2 for p in ring)/96
            ratios.append(radius_squared/(tip[2]-ring[0][2]))
        # A rounded pole has r^2 proportional to axial distance; the previous
        # smoothstep taper collapsed into a cusp and fails this ratio check.
        self.assertLess(max(ratios)/min(ratios), 1.03)
