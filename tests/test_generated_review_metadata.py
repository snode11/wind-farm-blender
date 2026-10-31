"""New RGB and atlas outputs cannot inherit a historical visual review."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
import numpy as np
from PIL import Image
from wfrl.nrel_reconstruction.observations import segment_rgb


class GeneratedReviewTests(unittest.TestCase):
    def test_new_rgb_annotation_has_explicit_unreviewed_identity(self):
        rgb=np.zeros((64,64,3),np.uint8);rgb[10:50,20:40]=210
        _,annotation=segment_rgb(rgb,'C2')
        self.assertIsNone(annotation.get('reviewer'))
        self.assertEqual(annotation['visual_review']['status'],'NOT_REVIEWED')
        self.assertEqual(annotation['visual_review']['rgb_sha256'],hashlib.sha256(rgb.tobytes()).hexdigest())

    def test_new_atlas_package_has_unreviewed_current_file_hashes(self):
        try: import cv2
        except ImportError: self.skipTest('OpenCV packaging environment required')
        path=Path(__file__).resolve().parents[1]/'scripts/reconstruction/prepare_mappo_same_source_texture.py'
        spec=importlib.util.spec_from_file_location('review_package_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'output';out.mkdir();(out/'texture').mkdir()
            texture={'atlas':{'H':4,'W':8,'dr':1,'r0':0},'params':{},'input_contract':{
                'frames':[{'saved_calibration_checked':True}]*601,'reconstruction_input_provenance_checked':True}}
            for name,data in [('texture/texture.json',texture),('recon.json',{}),('model_compatibility.json',{}),
                              ('input/input_bundle.json',{}),('source_integrity.json',{'source_files':{},'model_compatibility':{
                                  'vendor_model':{'sha256':'a'*64},'source_model':{},'texture_model':{}},
                                  'source_capture':{'source_hashes':{'assets/mappo/manifest.json':'b'*64}}})]:
                module.write_json(out/name,data)
            for name in ['model.py','atlas.py']:
                file=out/'engine/blade_recon0.2'/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('# fixture\n')
            has=np.ones((3,4,8),bool)
            np.savez(out/'texture/texture_data.npz',has=has,nview=has.astype(int),val=has.astype(float),raw=has.astype(float),wmax=has.astype(float))
            for b in (1,2,3): Image.fromarray(np.full((4,8,4),255,np.uint8)).save(out/f'texture/blade{b}_tex.png')
            asset=root/'asset';module.package(NS(asset=asset),out)
            quality=json.loads((asset/'texture_quality.json').read_text())
            review=quality['visual_inspection']
            self.assertEqual(review['status'],'NOT_REVIEWED')
            self.assertEqual(review['atlas_sha256'],{f'blade{b}_tex.png':module.digest(out/f'texture/blade{b}_tex.png') for b in (1,2,3)})
            frozen=(out/'texture_quality.json').read_bytes()
            Image.fromarray(np.full((4,8,4),254,np.uint8)).save(out/'texture/blade1_tex.png')
            with self.assertRaises(ValueError): module.package(NS(asset=asset),out)
            self.assertEqual((out/'texture_quality.json').read_bytes(),frozen)

if __name__=='__main__':unittest.main()
