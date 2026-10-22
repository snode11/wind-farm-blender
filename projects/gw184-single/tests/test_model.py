import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gw184.model import (read_config, motion_at, boundaries, frame_times, blade_mesh,
                         centreline_at, reference_rings, source_stations, smooth_interpolate)


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.g,self.m,self.c=read_config()

    def test_full_timeline_and_hold(self):
        self.assertEqual(boundaries(self.m),[0,10,100,220,310,320])
        times=frame_times(0,320,30)
        self.assertEqual(len(times),9600)
        self.assertEqual(len([t for t in times if 100<=t<220]),3600)
        self.assertTrue(all(motion_at(t,self.m)['rpm']==8 for t in (100,101,219.999)))
        self.assertEqual(motion_at(320,self.m)['rpm'],0)
        self.assertEqual(motion_at(320,self.m)['azimuth_deg'],28*360)

    def test_actual_angular_velocity_matches_rpm(self):
        for t in (1,10,11,38.25,99,100,130,219,220,254.1,309,310,319):
            dt=1e-4
            derivative=(motion_at(t+dt,self.m)['azimuth_deg']-motion_at(t-dt,self.m)['azimuth_deg'])/(2*dt)
            self.assertAlmostEqual(derivative,motion_at(t,self.m)['rpm']*6,places=5)

    def test_continuous_pitch_and_rotation(self):
        for t in boundaries(self.m)[1:-1]:
            for key in ('rpm','pitch_deg','azimuth_deg'):
                self.assertAlmostEqual(motion_at(t-1e-7,self.m)[key],motion_at(t+1e-7,self.m)[key],places=4)
        self.assertEqual(motion_at(0,self.m)['pitch_deg'],90)
        self.assertEqual(motion_at(100,self.m)['pitch_deg'],0)
        self.assertEqual(motion_at(320,self.m)['pitch_deg'],90)
        state=motion_at(57.1,self.m)
        motion_at(319,self.m)
        self.assertEqual(motion_at(57.1,self.m),state)

    def test_invalid_timing(self):
        for args in ((0,0,30),(-1,1,30),(0,.01,30),(0,1,0),(0,1,float('nan'))):
            with self.assertRaises(ValueError):frame_times(*args)
        for t in (-1,321,float('nan')):
            with self.assertRaises(ValueError):motion_at(t,self.m)

    def test_geometry_contract_and_closed_mesh(self):
        vertices,faces,stations=blade_mesh(self.g)
        self.assertEqual(min(v[2] for v in vertices),0)
        self.assertEqual(max(v[2] for v in vertices),90)
        self.assertAlmostEqual(max(s['chord_m'] for s in stations),5.6,places=10)
        self.assertEqual(2*(self.g['root_radius_m']+90),184)
        self.assertAlmostEqual(stations[0]['chord_m'],3.2)
        edges={}
        for face in faces:
            self.assertEqual(len(face),len(set(face)))
            for a,b in zip(face,face[1:]+face[:1]):
                key=tuple(sorted((a,b)))
                edges[key]=edges.get(key,0)+1
        self.assertTrue(all(count==2 for count in edges.values()))
        self.assertTrue(all(math.isfinite(x) for v in vertices for x in v))

    def test_camera_target_parameters(self):
        self.assertEqual(self.c['lens_mm'],8)
        self.assertEqual(self.c['resolution'],[3840,2160])
        hfov,vfov=[math.degrees(2*math.atan(s/16)) for s in self.c['sensor_mm']]
        self.assertAlmostEqual(hfov,51.282,places=3)
        self.assertAlmostEqual(vfov,30.2192,places=3)

    def test_prebend_is_geometric_and_root_is_straight(self):
        vertices,_,_=blade_mesh(self.g)
        self.assertEqual(vertices[-1],(-3.,0.,90.))
        self.assertEqual(centreline_at(0,self.g),(0.,0.,0.))
        straight=blade_mesh(dict(self.g,prebend_m=0))[0]
        for bent,plain in zip(vertices[::100],straight[::100]):
            offset=centreline_at(bent[2],self.g)
            self.assertAlmostEqual(bent[0]-plain[0],offset[0])
            self.assertEqual(bent[1:],plain[1:])
        for key in ('precone_deg','shaft_tilt_deg'):
            with self.assertRaises(ValueError):blade_mesh(dict(self.g,**{key:1}))

    def test_reference_sections_have_camber(self):
        for thickness,ring in reference_rings(128):
            self.assertEqual(len(ring),128)
            self.assertTrue(all(math.isfinite(x) for pair in ring for x in pair))
            self.assertGreater(max(abs(ring[j][1]+ring[-j][1]) for j in range(1,64)),.015)
            actual=max(y for _,y in ring)-min(y for _,y in ring)
            self.assertAlmostEqual(actual,thickness,delta=.005)

    def test_station_interpolation_preserves_source_and_bounds(self):
        aero,twist=source_stations()
        for rows,cols in ((aero,(1,2)),(twist,(1,))):
            for col in cols:
                for row in rows:
                    self.assertAlmostEqual(smooth_interpolate(rows,row[0],col),row[col])
                for a,b in zip(rows,rows[1:]):
                    for i in range(11):
                        value=smooth_interpolate(rows,a[0]+(b[0]-a[0])*i/10,col)
                        self.assertGreaterEqual(value,min(a[col],b[col])-1e-10)
                        self.assertLessEqual(value,max(a[col],b[col])+1e-10)

    def test_start_and_stop_pitch_direction_at_every_video_frame(self):
        start=[motion_at(t,self.m)['pitch_deg'] for t in frame_times(10,90,30)]+[0.]
        stop=[motion_at(t,self.m)['pitch_deg'] for t in frame_times(220,90,30)]+[90.]
        self.assertTrue(all(a>=b for a,b in zip(start,start[1:])))
        self.assertTrue(all(a<=b for a,b in zip(stop,stop[1:])))
        for a,b in zip(start,stop):self.assertAlmostEqual(a+b,90.)

if __name__=='__main__':unittest.main()
