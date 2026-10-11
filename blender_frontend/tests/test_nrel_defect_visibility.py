"""Independent projection/union examples and the NREL absolute clock."""
import importlib
import sys
from pathlib import Path
import unittest
from test_nrel_defects import ALIAS
visibility=importlib.import_module(ALIAS+'.nrel_defects.defect_visibility')
timeline=importlib.import_module(ALIAS+'.nrel_defects.defect_timeline')

class NrelProjectionTests(unittest.TestCase):
    def test_shared_and_overlapping_triangles_have_exact_union_area(self):
        square=[[(0,0),(2,0),(2,2)],[(0,0),(2,2),(0,2)]]
        shifted=[[(x+1,y+1) for x,y in p] for p in square]
        self.assertAlmostEqual(visibility.union_area(square*2),4.)
        self.assertAlmostEqual(visibility.union_area(square+shifted),7.)
        diamond=[(-1,0),(0,1),(1,0),(0,-1)]
        self.assertAlmostEqual(visibility.union_area([diamond,[(-.5,-.5),(.5,-.5),(.5,.5),(-.5,.5)]]),2.)

    def test_half_occlusion_and_subpixel_width_remain_distinct_from_recognition(self):
        camera=dict(origin=(0,0,5),axes=((1,0,0),(0,1,0),(0,0,-1)),fx=100.,fy=100.,cx=49.5,cy=49.5,width=100,height=100,near=.01,far=100)
        a,b,c,d=(-.002,-1,0),(.002,-1,0),(.002,1,0),(-.002,1,0)
        support=dict(triangles=[(a,b,c),(a,c,d)],normals=[(0,0,1)]*2,width_sections=[(a,b)],centerline=[(0,-1,0),(0,1,0)])
        def blocker(origin,direction,maximum):
            hit=(1-origin[2])/direction[2]
            return hit if direction[0]<0 and hit<maximum else None
        row=visibility.analyze_support(support,camera,blocker,sampling_step_px=.01)
        self.assertEqual(row['status'],'PARTIAL')
        self.assertAlmostEqual(row['visible_fraction_in_frame'],.5,places=6)
        self.assertAlmostEqual(row['width_px']['max'],.08)
        self.assertEqual(row['appearance_review']['status'],'NOT_REVIEWED')

    def test_source_start_maps_117_seconds_to_frame_one(self):
        mapping=timeline.FrameMapping(117.,.1,20.,60.,117.)
        self.assertEqual(mapping.sample(0)['blender_frame'],1)
        self.assertEqual(mapping.sample(1)['blender_frame'],4)
        self.assertEqual(mapping.to_dict()['blender_start_time_s'],117.)
        with self.assertRaises(ValueError):timeline.FrameMapping(116.,1.,20.,60.,117.)

if __name__=='__main__':unittest.main()
