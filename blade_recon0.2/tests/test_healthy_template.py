"""Explicit fixed healthy-design opt-in and immutable parameterization contract."""
import copy, hashlib, json
from pathlib import Path
import sys, tempfile, unittest
import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model import BladeTemplate, Rotor, TurbineConfig, default_state, healthy_template_sha256
from input_adapter import InputSequence


def fixed_template(cfg):
    """Construct a known design fixture analytically, never a posed source mesh."""
    tpl=BladeTemplate(cfg)
    c=(tpl.x_pa[:,None]-tpl.foils[...,0])*tpl.chord[:,None]
    t=tpl.foils[...,1]*tpl.chord[:,None]
    beta=np.radians(tpl.twist)[:,None]
    offsets=np.stack([c*np.cos(beta)-t*np.sin(beta),c*np.sin(beta)+t*np.cos(beta)],axis=2)
    return {'schema':'blade-healthy-loft.v1','coordinate_contract':'leading_upwind_radial_metres',
            'ring_contract':'TE_pressure_LE_suction_cosine_chord',
            'dimension_contract':{k:getattr(cfg,k) for k in ('hub_radius_m','tip_radius_m','n_sections','n_ring')},
            'radii_m':tpl.r.tolist(),'offsets_m':offsets.tolist(),'section_chord_m':tpl.chord.tolist(),
            'provenance':{'kind':'fixed_design_asset','geometry_asset_sha256':'a'*64,'builder_sha256':'b'*64},
            'tip_policy':'source_illustrative_taper_with_1um_terminal_ring'}


def make_bundle(root,cfg,declaration):
    (root/'rgb').mkdir(); (root/'masks').mkdir()
    cv2.imwrite(str(root/'rgb/000042.png'),np.full((12,16,3),100,np.uint8))
    cv2.imwrite(str(root/'masks/000042.png'),np.zeros((12,16),np.uint8))
    T=np.eye(4);T[2,3]=200
    raw={'schema':'blade-surface-input.v1','extrinsic_from_frame':'model',
         'pixel_coordinates':'original_image_pixel_centers','color_order':'RGB',
         'turbine':cfg.to_dict(),'healthy_template':declaration,
         'frames':[{'frame':42,'t':4.2,'cameras':[{'name':'A','W':16,'H':12,
                   'K':[[20,0,7.5],[0,20,5.5],[0,0,1]],'T_cv_from_model':T.tolist(),
                   'rgb':'rgb/000042.png','mask':'masks/000042.png'}]}]}
    (root/'input_bundle.json').write_text(json.dumps(raw))
    return raw


class HealthyTemplateTests(unittest.TestCase):
    def setUp(self):
        self.cfg=TurbineConfig(n_sections=8,n_ring=12,prebend_tip_m=0.)
        self.payload=fixed_template(self.cfg)

    def active(self,payload=None):
        cfg=TurbineConfig.from_dict(self.cfg.to_dict())
        cfg.healthy_template=copy.deepcopy(self.payload if payload is None else payload)
        cfg.healthy_template_sha256=healthy_template_sha256(cfg.healthy_template)
        return cfg

    def test_default_serialization_retains_legacy_contract(self):
        self.assertNotIn('healthy_template',self.cfg.to_dict())
        self.assertNotIn('healthy_template_sha256',self.cfg.to_dict())

    def test_declared_rest_prebend_rotates_with_pitch(self):
        cfg=self.active();cfg.prebend_tip_m=1.
        rotor=Rotor(cfg)
        unbent=Rotor(self.active())
        for psi,pitch in ((0.,0.),(73.,2.),(143.,-4.)):
            _,axis=rotor.blade(psi,pitch,0.,0.);_,base=unbent.blade(psi,pitch,0.,0.)
            radial=np.cos(np.radians(psi))*rotor.e_up+np.sin(np.radians(psi))*rotor.e_lat
            rot=cfg.spin*(-np.sin(np.radians(psi))*rotor.e_up+np.cos(np.radians(psi))*rotor.e_lat)
            nb=-np.sin(np.radians(cfg.cone_deg))*radial+np.cos(np.radians(cfg.cone_deg))*rotor.n
            direction=-np.sin(np.radians(pitch))*rot+np.cos(np.radians(pitch))*nb
            np.testing.assert_allclose(axis-base,rotor.tpl.prebend[:,None]*direction,atol=2e-14)

    def test_embedded_design_roundtrip_matches_forward_state_without_answers(self):
        source=Rotor(self.cfg);active=Rotor(self.active())
        state=default_state(78.,4.)
        state[4:10]=[2.,1.,3.,.2,-.4,.6];state[10:13]=[.1,-.2,.3]
        np.testing.assert_allclose(active.forward(state)[0],source.forward(state)[0],atol=2e-14)
        decoded=Rotor(TurbineConfig.from_dict(json.loads(json.dumps(active.cfg.to_dict()))))
        np.testing.assert_array_equal(decoded.forward(state)[0],active.forward(state)[0])
        before=active.tpl.r.copy();active.forward(default_state(20.))
        np.testing.assert_array_equal(active.tpl.r,before)

    def test_wrong_dimension_hash_dynamic_extra_and_nonfinite_are_rejected(self):
        cfg=self.active();cfg.healthy_template_sha256='f'*64
        with self.assertRaisesRegex(ValueError,'integrity'):Rotor(cfg)
        cfg=self.active();cfg.n_sections+=1
        with self.assertRaisesRegex(ValueError,'mismatch'):Rotor(cfg)
        bad=copy.deepcopy(self.payload);bad['dynamic_source_states']=[[0]*13]
        with self.assertRaisesRegex(ValueError,'fixed-design schema'):Rotor(self.active(bad))
        bad=copy.deepcopy(self.payload);bad['offsets_m'][0][0][0]=float('nan')
        with self.assertRaises(ValueError):self.active(bad)

    def test_declared_design_is_read_logged_embedded_and_bounds_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);asset=root/'healthy.json';asset.write_text(json.dumps(self.payload))
            sha=hashlib.sha256(asset.read_bytes()).hexdigest()
            make_bundle(root,self.cfg,{'path':'healthy.json','sha256':sha})
            sequence=InputSequence(root,max_frames=1)
            self.assertEqual(sequence.cfg.healthy_template_sha256,healthy_template_sha256(self.payload))
            self.assertEqual(sequence.read().frame,42)
            logs=[r for r in sequence.access_log if r['role']=='explicit_fixed_healthy_design']
            self.assertEqual(len(logs),1);self.assertEqual(logs[0]['sha256'],sha)
            Rotor(TurbineConfig.from_dict(sequence.cfg.to_dict()))

    def test_health_file_mismatch_and_truth_symlink_are_rejected(self):
        for mode in ('hash','truth'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);asset=root/'healthy.json'
                if mode=='truth':
                    (root/'evaluation').mkdir();truth=root/'evaluation/truth.json'
                    truth.write_text(json.dumps(self.payload));asset.symlink_to(truth)
                else:asset.write_text(json.dumps(self.payload))
                sha=hashlib.sha256(asset.read_bytes()).hexdigest()
                make_bundle(root,self.cfg,{'path':'healthy.json','sha256':'0'*64 if mode=='hash' else sha})
                with self.assertRaisesRegex(ValueError,'integrity' if mode=='hash' else 'forbidden'):
                    InputSequence(root)

if __name__=='__main__':unittest.main(verbosity=2)
