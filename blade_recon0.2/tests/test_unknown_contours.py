"""Meaningful unknown-edge and declared-RGB identity regressions."""
from pathlib import Path
import hashlib, json, sys, tempfile, unittest
from types import SimpleNamespace
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'blade_recon0.2'))
from recon import Obs
from contour_support import UnknownContours

class UnknownTests(unittest.TestCase):
    def test_unknown_cut_does_not_create_an_image_contour_or_model_force(self):
        mask=np.zeros((64,64),np.uint8);mask[10:54,10:34]=255
        known=np.ones(mask.shape,bool);known[:,32:]=False
        observer=Obs(mask,known=known,n_pts=1000)
        self.assertTrue(observer.has)
        self.assertTrue((observer.pts[:,0]<31).all())
        self.assertEqual(float(observer.model_residual(np.array([[40.,30.]]))[0]),0.)
        self.assertGreater(float(observer.model_residual(np.array([[15.,30.]]))[0]),0.)

    def test_no_known_pixels_keeps_missing_observation(self):
        observer=Obs(np.full((12,12),255,np.uint8),known=np.zeros((12,12),bool))
        self.assertFalse(observer.has);self.assertEqual(len(observer.pts),0)

    def test_polygon_requires_original_rgb_hash_and_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);region={'kind':'unknown_contour','frame':42,'t':4.2,'camera':'A',
                'polygon_xy':[[3,0],[6,0],[6,7],[3,7]],'source_rgb':'A.png',
                'source_rgb_sha256':'rgb-hash','reason':'independently ambiguous RGB'}
            p=root/'ann.json'
            def bundle():
                return SimpleNamespace(manifest={'annotations':'ann.json','frames':[{'frame':42,'t':4.2,'cameras':[{'name':'A','W':8,'H':8,'rgb':'A.png'}]}]},
                    access_log=[{'path':'ann.json','role':'human_2D_annotations','sha256':hashlib.sha256(p.read_bytes()).hexdigest()},
                                {'path':'A.png','role':'RGB','sha256':'rgb-hash'}])
            p.write_text(json.dumps({'regions':[region]}))
            support=UnknownContours(root,bundle());self.assertFalse(support.known(42,'A',(8,8))[2,4])
            region['source_rgb_sha256']='wrong';p.write_text(json.dumps({'regions':[region]}))
            with self.assertRaisesRegex(ValueError,'hash mismatch'):UnknownContours(root,bundle())
            region['source_rgb_sha256']='rgb-hash';region['t']=4.3;p.write_text(json.dumps({'regions':[region]}))
            with self.assertRaisesRegex(ValueError,'time/camera mismatch'):UnknownContours(root,bundle())

if __name__=='__main__':unittest.main(verbosity=2)
