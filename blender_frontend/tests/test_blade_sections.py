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
