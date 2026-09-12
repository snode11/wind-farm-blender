from dataclasses import replace
from wfrl_blender.scene_model import SceneDTO, TurbineDTO


def test_runtime_parameters_do_not_change_geometry_identity():
    scene=SceneDTO.from_mapping({'layout':[{'id':'WT01','x':0,'y':0}]})
    assert scene.geometry_key()==replace(scene,wind_speed_mps=10,wind_direction_deg=320,dt_s=3,name='new',backend='fastfarm').geometry_key()
    for changed in (replace(scene,terrain='mountains'),replace(scene,turbines=(TurbineDTO('WT02',0,0),)),replace(scene,turbines=(TurbineDTO('WT01',504,0),))):
        assert scene.geometry_key()!=changed.geometry_key()
