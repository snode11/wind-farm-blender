"""Global model family contract and explicit legacy direct API compatibility."""
from pathlib import Path
import sys, unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'blade_recon0.2'))
from recon import Fitter, Obs, N_STATE
from model import Rotor,TurbineConfig
from camera import build_rig
class FullModelTests(unittest.TestCase):
    def setUp(self):
        rotor=Rotor(TurbineConfig(n_sections=8,n_ring=8))
        cams=build_rig(rotor,back_m=150,down_m=0,yaw_step_deg=10,hfov_deg=60,vfov_deg=60,W=32,H=32)
        self.fitter=Fitter(rotor,cams)
        self.empty=[Obs(np.zeros((c.H,c.W),np.uint8)) for c in cams]
    def test_full_model_preserves_all_three_global_second_bend_directions(self):
        p=np.array([80.]+[0.]*3+[3.]*3+[0.]*3+[.1,-.2,.3])
        np.testing.assert_array_equal(self.fitter.full(p),p)
        r=self.fitter.residuals(p,self.empty,p,0.)
        self.assertEqual(len(r[-2*N_STATE:]),26)
        x,std,result=self.fitter.solve(self.empty,p,p,max_nfev=2)
        self.assertEqual(len(x),13); self.assertEqual(len(std),13)
        self.assertEqual(result.observation_rank,0)
        self.assertFalse(result.reconstruction_valid)
    def test_legacy_direct_api_keeps_explicit_ten_direction_contract(self):
        p=np.array([80.]+[0.]*3+[3.]*3+[0.]*3)
        np.testing.assert_array_equal(self.fitter.full(p),np.r_[p,[0.]*3])
        x,std,result=self.fitter.solve(self.empty,p,p,max_nfev=2)
        self.assertEqual(len(x),10);self.assertEqual(len(std),10)
        self.assertFalse(result.reconstruction_valid)
        with self.assertRaises(ValueError):self.fitter.full(np.zeros(12))
        with self.assertRaises(ValueError):self.fitter.solve(self.empty,p,np.zeros(13),max_nfev=2)
if __name__=='__main__':unittest.main(verbosity=2)
