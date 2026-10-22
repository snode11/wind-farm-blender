"""Reject mismatched new reference data while preserving legacy replay support."""
import hashlib,json
import numpy as np
import pytest
from wfrl_blender.prebend import read_reference,bend_vertices
from wfrl_blender.deflection import compare


def fixture(tmp_path):
    z=np.linspace(0,61.5,49)
    reference=dict(schema='wfrl.blade-reference.v1',structural_module='BeamDyn',model='test derivative',
        curve=np.column_stack((-(z/61.5)**2,z*0,z,z*0)).tolist(),tip_local_m=[-1,0,63],
        definition='x=-(z/61.5)^2; y=0; metres; z from blade root',
        reference_state='independent zero-load stationary solve',precone_deg=-2.5)
    path=tmp_path/'blade-reference.json';path.write_text(json.dumps(reference))
    manifest=dict(schema='wfrl.farm-flex-review.v3',model=reference['model'],structural_module='BeamDyn',
        files={path.name:hashlib.sha256(path.read_bytes()).hexdigest()})
    return path,reference,manifest


def test_reference_curve_and_surface_bending(tmp_path):
    _,r,m=fixture(tmp_path)
    assert read_reference(tmp_path,m)==r
    np.testing.assert_allclose(bend_vertices([[0,0,1.5],[0,0,32.25],[0,0,63]],r),
        [[0,0,1.5],[-.25,0,32.25],[-1,0,63]])
    assert read_reference(tmp_path,dict(schema='wfrl.farm-flex-review.v2',files={})) is None


@pytest.mark.parametrize('mutation',['curve','model','rest','cone','hash'])
def test_reference_contract_rejects_cross_model_or_loaded_rest(tmp_path,mutation):
    path,r,m=fixture(tmp_path)
    if mutation=='curve':r['curve'][20][0]+=.1
    if mutation=='model':r['model']='other'
    if mutation=='rest':r['reference_state']='loaded frame zero'
    if mutation=='cone':r['precone_deg']=0
    path.write_text(json.dumps(r)+'\n')
    if mutation!='hash':m['files'][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):read_reference(tmp_path,m)


def test_beamdyn_components_include_axial_and_independent_reference():
    result=compare([2,3,5],[-1,0,3],np.eye(3),[3,3,2])
    np.testing.assert_allclose(result['error'],0)
    np.testing.assert_allclose(result['components'],[3,3,2])
