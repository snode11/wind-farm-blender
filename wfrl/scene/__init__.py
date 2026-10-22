"""场景层：用 YAML 描述风场，校验后驱动仿真后端。

在此之前，风场布局只能从 `wfcrl/environments/data_cases.py` 里预注册的常量表
里挑（`Dec_Turb3_Row1_Fastfarm` 这类环境名），用户无法自己造一个风场。本层把
关系反过来：场景文件是唯一事实来源，环境是从它生成的。

    from wfrl.scene import load_scene
    sc = load_scene("scenes/turb3_row.yaml")
    env = sc.make_env()          # 直接得到 WFCRL 环境
"""
from wfrl.scene.schema import (Scene, Turbine, SensorSpec, SceneError,
                               load_scene, dump_scene)


def __getattr__(name):
    # runtime 会 import fastfarm_driver（连带 wfcrl/MPI 检查），只在真要跑仿真时载入
    if name == "SceneRuntime":
        from wfrl.scene.runtime import SceneRuntime
        return SceneRuntime
    raise AttributeError(name)


__all__ = ["Scene", "Turbine", "SensorSpec", "SceneError",
           "load_scene", "dump_scene", "SceneRuntime"]
