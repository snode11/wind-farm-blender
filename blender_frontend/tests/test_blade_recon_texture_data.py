"""Small v0.2 atlas and saved dynamic-camera display contract regressions."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import numpy as np

from wfrl_blender.blade_recon_data import ReconSequence, TexturePackage
from wfrl_blender._vendor.blade_recon.model import TurbineConfig


def saved_data():
    return {'turbine': TurbineConfig(n_sections=4,n_ring=8).to_dict(),'fps':10,
            'frames':[{'frame':42+i,'t':4.2+i*.1,'state':[17.+i]+[0.]*12,
                       'observed_sections':[[0]*4]*3} for i in range(2)]}


def camera(tx):
    return {'name':'C1','W':64,'H':48,'K':[[50,0,32],[0,50,24],[0,0,1]],
            'T_cv_from_model':[[1,0,0,tx],[0,1,0,0],[0,0,1,100],[0,0,0,1]]}


def png(width,height):
    def chunk(kind,body):
        return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+
            chunk(b'IDAT',zlib.compress((b'\x00'+b'\x80\x80\x80\xff'*width)*height))+
            chunk(b'IEND',b''))


class TextureDataTests(unittest.TestCase):
    def make_texture(self,path,rotor):
        meta={'atlas':{'r0':float(rotor.tpl.r[0]),'r1':float(rotor.tpl.r[-1]),
                       'H':3,'W':4,'dr':float(rotor.tpl.r[-1]-rotor.tpl.r[0])/3},
              'n_ring':8,'n_sections':4,'params':{'register':False}}
        (path/'texture.json').write_text(json.dumps(meta))
        for b in range(3):
            (path/f'blade{b+1}_tex.png').write_bytes(png(4,3))
        return meta

    def test_all_three_images_and_atlas_metadata_validated(self):
        rotor=ReconSequence.from_data(saved_data()).rotor
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            self.make_texture(path,rotor)
            package=TexturePackage(path,rotor)
            self.assertEqual(len(package.images),3)
            self.assertEqual([f['blade'] for f in package.manifest['files']],[0,1,2])
            self.assertFalse(package.manifest['params']['register'])
            self.assertTrue(all(len(f['sha256'])==64 for f in package.manifest['files']))

    def test_missing_image_wrong_dimensions_and_wrong_radius_rejected(self):
        rotor=ReconSequence.from_data(saved_data()).rotor
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            meta=self.make_texture(path,rotor)
            (path/'blade2_tex.png').unlink()
            with self.assertRaisesRegex(ValueError,'Cannot read texture PNG'):
                TexturePackage(path,rotor)
            (path/'blade2_tex.png').write_bytes(png(4,2))
            with self.assertRaisesRegex(ValueError,'must be 8-bit RGBA'):
                TexturePackage(path,rotor)
            self.make_texture(path,rotor)
            meta['atlas']['r1']+=1
            meta['atlas']['dr']=(meta['atlas']['r1']-meta['atlas']['r0'])/3
            (path/'texture.json').write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,'radius range does not match'):
                TexturePackage(path,rotor)

    def test_dynamic_cameras_follow_original_samples(self):
        data=saved_data()
        for i,frame in enumerate(data['frames']):
            frame['cameras']=[camera(i)]
        sequence=ReconSequence.from_data(data)
        np.testing.assert_array_equal(sequence.source_frames,[42,43])
        np.testing.assert_allclose(sequence.times,[4.2,4.3])
        self.assertEqual(sequence.camera_frames[0][0]['T_cv_from_world'][0][3],0)
        self.assertEqual(sequence.camera_frames[1][0]['T_cv_from_world'][0][3],1)
        self.assertEqual(sequence.cameras,sequence.camera_frames[0])
        self.assertEqual(len(data['frames'][0]['cameras'][0]['T_cv_from_model']),4)

    def test_incomplete_or_reordered_dynamic_cameras_rejected(self):
        data=saved_data()
        data['frames'][0]['cameras']=[camera(0)]
        with self.assertRaisesRegex(ValueError,'missing for some'):
            ReconSequence.from_data(data)
        data['frames'][1]['cameras']=[dict(camera(1),name='C2')]
        with self.assertRaisesRegex(ValueError,'names/order'):
            ReconSequence.from_data(data)
        data['frames'][1]['cameras']=[copy.deepcopy(camera(1))]
        del data['frames'][1]['cameras'][0]['T_cv_from_model']
        data['frames'][1]['cameras'][0]['T_cv_from_world']=camera(1)['T_cv_from_model']
        with self.assertRaisesRegex(ValueError,'T_cv_from_model'):
            ReconSequence.from_data(data)


if __name__=='__main__':
    unittest.main()
