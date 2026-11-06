"""Studio 启动入口与窗口组件导出。

主窗口把工作线程的 Snapshot 搬到 3D 与面板上，不包含物理或算法。
工作线程运行 FAST.Farm 与 PPO，主线程只读快照并更新 actor。
_tick（120 ms）取快照更新面板与位姿，_render（33 ms）按真实流逝时间推进转子相位。

运行：
    python -m wfrl.studio.app --scene scenes/turb3_row.yaml
    python -m wfrl.studio.app --scene scenes/turb3_row.yaml --train --iters 4
"""
from __future__ import annotations

import logging
import sys

from qtpy import QtCore, QtWidgets

from wfrl.scene.schema import load_scene
from wfrl.studio import channels as _channels
from wfrl.studio import lifecycle as _lifecycle
from wfrl.studio import panels as _panels
from wfrl.studio import turbine_window as _turbine_window
from wfrl.studio import window as _window


os = _panels.os
time = _lifecycle.time
Optional = _window.Optional
np = _panels.np
QtGui = _panels.QtGui
QtInteractor = _window.QtInteractor
Fidelity = _panels.Fidelity
SafetyLimiter = _panels.SafetyLimiter
Scene = _window.Scene
Trainer = _window.Trainer
SceneView = _window.SceneView
_use_system_fixed_font = _panels._use_system_fixed_font
ScenePanel = _panels.ScenePanel
ChannelPanel = _panels.ChannelPanel
TrainPanel = _panels.TrainPanel
_Curve = _panels._Curve
SafetyPanel = _panels.SafetyPanel
ControlPanel = _panels.ControlPanel
TurbineWindow = _turbine_window.TurbineWindow
StudioWindow = _window.StudioWindow


_LOG = logging.getLogger(__name__)
_channels._LOG = _LOG
_lifecycle._LOG = _LOG
_turbine_window._LOG = _LOG


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="WFRL Studio")
    ap.add_argument("--scene", required=True, help="场景 YAML")
    ap.add_argument("--train", action="store_true", help="启动后立即开始训练")
    ap.add_argument("--iters", type=int, default=None)
    ap.add_argument("--n-steps", type=int, default=None)
    ap.add_argument("--warmup", type=int, default=None)
    ap.add_argument("--fast", action="store_true",
                    help="快迭代预设：iters=2 n_steps=16 warmup=1，单次 spawn 跑完，"
                         "只用于冲流程/看可视化，不出结论")
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--replay", action="store_true",
                    help="回放模式：加载 --ckpt 的策略，用确定性动作 (dist.mean) "
                         "跑一回合并可视化，不做训练更新")
    ap.add_argument("--replay-steps", type=int, default=400,
                    help="回放时跑多少步（默认 400）")
    ap.add_argument("--demo", action="store_true",
                    help="演示模式：预设启停循环（pitch 90↔0°）+ 偏航变化（0→30°），"
                         "不加载 checkpoint，用于演示 3D 可视化效果")
    ap.add_argument("--demo-cycles", type=int, default=2,
                    help="演示模式循环次数（默认 2）")
    ap.add_argument("--no-wake", action="store_true",
                    help="不建 FLORIS proxy 尾流平面（省一次稳态求解）")
    args = ap.parse_args(argv)

    # --fast 只为未提供的训练参数选择快速预设，显式值始终优先。
    if args.iters is None:
        args.iters = 2 if args.fast else 8
    if args.n_steps is None:
        args.n_steps = 16 if args.fast else 32
    if args.warmup is None:
        args.warmup = 1 if args.fast else 4

    scene = load_scene(args.scene)
    app = QtWidgets.QApplication(sys.argv)
    win = StudioWindow(scene, wake=not args.no_wake, train_kw=dict(
        iters=args.iters, n_steps=args.n_steps, warmup_steps=args.warmup,
        ckpt_path=args.ckpt, replay=args.replay, replay_steps=args.replay_steps,
        demo=args.demo, demo_cycles=args.demo_cycles))
    win.show()
    if args.train or args.replay or args.demo:
        QtCore.QTimer.singleShot(300, win._start)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
