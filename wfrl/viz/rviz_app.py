"""
RViz 类风场可视化桌面应用（PyVistaQt，无 ROS 依赖）。

核心 RViz 范式：
  - 3D 场景可鼠标轨道/缩放/平移；机组可点击选中并在地面拖拽重定位（回填 FLORIS 重算尾流）
  - "channel"（等价 ROS topic）：数据源每步把 yaw/power/vibration/wake 发布到各通道，
    面板订阅通道、数据到就刷新。后台 QTimer 驱动"数据流"，随时可开关某路流。

运行：
    python rviz_app.py                 # 自由运行（yaw 自动轻摆，看尾流随动）
    python rviz_app.py --model ippo    # 用训练好的 IPPO 策略驱动 yaw
    python rviz_app.py --model central # 用训练好的集中式 critic MAPPO 驱动

依赖：pyvistaqt PyQt5（已装）。
"""
from wfrl.viz import rviz_channels as _channels
from wfrl.viz import rviz_cli as _cli
from wfrl.viz import rviz_panels as _panels
from wfrl.viz import rviz_runtime as _runtime
from wfrl.viz import rviz_sources as _sources
from wfrl.viz import rviz_window as _window

BufferHub = _channels.BufferHub
ChannelHub = _channels.ChannelHub
threading = _channels.threading

QtWidgets = _cli.QtWidgets
argparse = _cli.argparse
main = _cli.main
sys = _cli.sys

CALIB = _panels.CALIB
ChannelPanel = _panels.ChannelPanel
TelemetryPanel = _panels.TelemetryPanel
_finite_mean = _panels._finite_mean
_finite_min = _panels._finite_min

_headless_loop = _runtime._headless_loop
_report_inflow = _runtime._report_inflow
_run_headless = _runtime._run_headless
make_fastfarm_policy_fn = _runtime.make_fastfarm_policy_fn
make_policy_fn = _runtime.make_policy_fn
os = _runtime.os
paths = _runtime.paths

FarmSource = _sources.FarmSource
FastFarmSource = _sources.FastFarmSource
envs = _sources.envs
extract_wake_plane = _sources.extract_wake_plane
get_floris = _sources.get_floris
mm = _sources.mm
np = _sources.np

MainWindow = _window.MainWindow
QtCore = _window.QtCore
QtInteractor = _window.QtInteractor
TURB_SCALE = _window.TURB_SCALE
ThreadPoolExecutor = _window.ThreadPoolExecutor
_cjk_font = _window._cjk_font
add_vtk_wake_plane = _window.add_vtk_wake_plane
add_wake_plane = _window.add_wake_plane
build_turbine = _window.build_turbine
load_turbine_geometry = _window.load_turbine_geometry
move_turbine_actors = _window.move_turbine_actors
pv = _window.pv
turbine_actors = _window.turbine_actors
update_turbine = _window.update_turbine
vtk_plane_scalars = _window.vtk_plane_scalars

del _channels, _cli, _panels, _runtime, _sources, _window


if __name__ == "__main__":
    main()
