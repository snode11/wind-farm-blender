from wfrl_blender.scene_model import SceneDTO


def test_scene_dto_preserves_real_meter_layout():
    scene = SceneDTO.from_mapping({"name": "turb3_demo", "backend": "demo", "layout": [{"id": "T1", "x": 504, "y": 31.5}], "inflow": {"speed": 8, "direction": 270}})
    assert scene.turbines[0].x_m == 504.0
    assert scene.turbines[0].y_m == 31.5
    assert scene.wind_speed_mps == 8.0
