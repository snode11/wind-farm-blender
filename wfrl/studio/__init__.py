"""Studio：场景驱动的桌面应用层。

与 `wfrl/viz/rviz_app.py` 的分工：rviz_app 是从 `env_id` 出发的既有演示程序，
布局来自预注册常量表；studio 从**场景文件**出发，机位、传感器、通道全部由 YAML
决定，是 D1~D2 的交付路径。两者共用 `viz/animate.py` 的机组几何与
`viz/field.py` 的尾流平面，不重复实现。

分层（下层不知道上层存在）：

    view.py      Scene → 3D 场景图。不碰 Qt，也不碰 MPI，能离屏验收
    trainer.py   MAPPO 跑后台线程，只写纯值快照。不碰 Qt，能无头跑
    app.py       主窗口，只做接线：把快照搬到 3D 与四个面板上

`app` 是唯一 import Qt 的模块，所以前两层的验收脚本不需要显示服务器。
"""
from wfrl.studio.trainer import Snapshot, Trainer
from wfrl.studio.view import SceneView, floris_proxy, TURB_SCALE

__all__ = ["SceneView", "Snapshot", "Trainer", "floris_proxy", "TURB_SCALE"]
