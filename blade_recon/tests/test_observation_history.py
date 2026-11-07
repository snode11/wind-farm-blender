"""The ten-parameter contour baseline keeps only accepted image motion."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recon


class ObservationHistoryTests(unittest.TestCase):
    def test_real_synthetic_contour_can_initialize_phase(self):
        from camera import build_rig
        from synth import render
        rotor=recon.Rotor(recon.TurbineConfig(n_sections=8,n_ring=8))
        cam=build_rig(rotor,back_m=150,down_m=0,yaw_step_deg=0,hfov_deg=60,vfov_deg=60,W=192,H=192)[1]
        _,mask=render(cam,rotor,np.array([80]+[0]*3+[3]*3+[0]*6),rotor.tpl.faces(),np.array([1.,0,0]),noise=0)
        fitter=recon.Fitter(rotor,[cam]);obs=[recon.Obs(mask)]
        x0=recon.PRIOR_MU.copy();x0[0]=recon.init_psi(fitter,obs)
        x,std,result=fitter.solve(obs,x0,x0,w_prior=0,max_nfev=150)
        self.assertTrue(result.success and result.reconstruction_valid)
        self.assertTrue(np.isfinite(std[0]))
        self.assertLess(abs(x[0]-80),2.)

    def test_empty_images_have_no_phase_or_zero_uncertainty(self):
        camera = recon.Camera.from_dict(dict(name='C1', W=64, H=64,
            K=[[50,0,32],[0,50,32],[0,0,1]], T_cv_from_world=[[1,0,0,0],[0,1,0,0],[0,0,1,200]]))
        fitter = recon.Fitter(recon.Rotor(recon.TurbineConfig(n_sections=4, n_ring=8)), [camera])
        obs = [recon.Obs(np.zeros((64,64), np.uint8))]
        self.assertIsNone(recon.init_psi(fitter, obs))
        x, std, result = fitter.solve(obs, recon.PRIOR_MU.copy(), recon.PRIOR_MU.copy())
        self.assertFalse(result.success)
        self.assertTrue(np.isnan(std).all())

    def test_rejected_frames_do_not_initialize_or_advance_motion_history(self):
        visible = np.zeros((64,64), np.uint8); visible[16:48,20:44] = 255
        images = iter([np.zeros((64,64), np.uint8)] + [visible]*4 + [None])
        solves, updates = [], []
        def solve(fitter, obs, x0, pred, **kwargs):
            index = len(solves); solves.append((x0.copy(), pred.copy()))
            x = recon.PRIOR_MU.copy(); x[0] = [80, 100, 86, 92][index]
            return x, np.ones(10), NS(success=index != 1, reconstruction_valid=index != 1, cost=1., nfev=1)
        meta = dict(fps=10, turbine=dict(n_sections=4, n_ring=8), cameras=[dict(name='C1', W=64, H=64,
            K=[[50,0,32],[0,50,32],[0,0,1]], T_cv_from_world=[[1,0,0,0],[0,1,0,0],[0,0,1,200]])])
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(recon, 'load_json', return_value=meta), \
                 patch.object(recon, 'FrameSource', return_value=NS(read=lambda: next(images))), \
                 patch.object(recon, 'segment', side_effect=lambda img, _: img), \
                 patch.object(recon, 'init_psi', return_value=80.), \
                 patch.object(recon.Fitter, 'solve', solve), \
                 patch.object(recon.Fitter, 'in_frame', return_value=(np.ones(3)*4, np.ones(3)*4)), \
                 patch.object(recon.Fitter, 'observed_sections', return_value=np.ones((3,4),bool)), \
                 patch.object(recon.PhysPrior, 'update', side_effect=lambda *args: updates.append(args)), \
                 patch.object(sys, 'argv', ['recon.py','--data',directory,'--out',directory]), \
                 contextlib.redirect_stdout(io.StringIO()):
                recon.main()
            rows = json.loads((Path(directory)/'recon.json').read_text())['frames']
        self.assertFalse(rows[0]['image_initialized'])
        self.assertIsNone(rows[0]['rpm'])
        self.assertFalse(rows[2]['reconstruction_valid'])
        self.assertIsNone(rows[2]['rpm'])
        self.assertNotIn('std', rows[0])
        self.assertTrue(all(v is None for v in rows[0]['observation_std']))
        self.assertAlmostEqual(rows[3]['rpm_observation_dt'], .2)
        self.assertAlmostEqual(rows[3]['rpm'], 5.)
        self.assertAlmostEqual(rows[4]['rpm'], 6.)
        self.assertEqual(len(updates), 3)
        self.assertEqual(len(solves), 4)

    def test_contour_without_projected_image_information_is_not_accepted(self):
        camera = recon.Camera.from_dict(dict(name='C1', W=64, H=64,
            K=[[50,0,32],[0,50,32],[0,0,1]], T_cv_from_world=[[1,0,0,0],[0,1,0,0],[0,0,1,-1000]]))
        fitter = recon.Fitter(recon.Rotor(recon.TurbineConfig(n_sections=4,n_ring=8)), [camera])
        mask=np.zeros((64,64),np.uint8);mask[16:48,20:44]=255
        obs=[recon.Obs(mask)]
        self.assertTrue(obs[0].has)
        _,std,result=fitter.solve(obs,recon.PRIOR_MU.copy(),recon.PRIOR_MU.copy(),w_prior=0)
        self.assertFalse(result.reconstruction_valid)
        self.assertTrue(np.isnan(std[0]))

if __name__ == '__main__': unittest.main()
