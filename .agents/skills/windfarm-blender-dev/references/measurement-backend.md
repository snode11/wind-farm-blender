# 测量、后端与协议

用于雷达读数／报警、快照曲线、DisXY、Blender Bridge、连接恢复及启动器任务。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| 雷达读数、报警与计算 | `clearance_replay.py`、`clearance_visual.py`、`radar_feedback.py`；仓库 `wfrl/lidar/`、`scripts/lidar/` | [结果合同](../../../../wfrl/lidar/REPLAY_FORMAT.md)、[算法](../../../../wfrl/lidar/README.md)、[计算流程](../../../../scripts/lidar/README.md) |
| 后端连接与训练 | `runtime.py`、`transport.py`、`panels/run.py`；仓库 `wfrl/blender_bridge/` | [训练入口与合同](../../../../docs/training/MAPPO_SETUP.md) |

## 雷达来源与统计

- B2 主统计只用 B2，分母为全部 expected 样本，偏差为同次估计减真值；不借其他叶片或真值补缺。
- 双束同刻、同叶片配对，S1 报警独立；`hub-axis.v1`／`hub-tls.v1` 保留方法、来源及结果层身份。
- 缺失、过期、空统计不补零，缺包、损坏或身份不符清旧读数；结构叶尖、展示表面、测量参考和光束长度分开，无读数或无报警不等于安全。
- 内置雷达结果层共享 `assets/mappo/`，换源须匹配 sidecar；外部便携双束带自身 `source/`。
- normal/close 是外部刚性示意，其结果包和原始 VTP 不随扩展提供。
- 交付核对包身份及适用安装，上传后遵守 AGENTS.md；打包不等于物理结果生成、科学验收或公开发布。

## 单位、时间与有效性

- 快照与曲线按规范协议校验 `rotor_speed=rpm`、`yaw=deg`、`pitch=deg`、`power=MW`，无效记录也须带正确单位；不静默改标签。
- DisXY 按源年龄加本地单调时钟判定过期，非有效、断开、换 run／身份／模式时撤回旧图；显式不匹配当前 `run_id` 的尾流被忽略，无 run 标识的兼容消息不能据此区分旧运行。
- 失效隐藏保留渲染及各 View Layer 的用户隐藏状态；过期期间显式 DisXY 开关决定恢复后的显示选择，其他图层开关不得覆盖它。

字段、单位及跨层转换以仓库 `wfrl/blender_bridge/messages.py`、[结果合同](../../../../wfrl/lidar/REPLAY_FORMAT.md)及相应生产／消费测试为准；坐标与变换按项目接口核对。外部风场模型的定义不能直接作为本项目接口约定。

## 实时场景归属

- 实时后端绑定连接时的所属场景；快照、姿态、来流及尾流不随活动场景迁移，非活动场景构建须使用所属 Scene／View Layer 上下文。
- 每次轮询前核对归属，所属场景删除时即使没有消息也断开并保留未确认状态，不自动认领另一场景；显式重连才可绑定当前非手动场景，保留会话身份与命令水位。

## 连接、作业与命令身份

- `BridgeServer.poll()` 无客户端仍推进；快照／统计与可靠生命周期／安全／错误事件分队列，溢出报 `EVENT_OVERFLOW` 并失败停止，重连保留等待年龄但不保证完整观察历史。
- `state_revision` 握手覆盖的生命周期只进入诊断，不回退当前状态；新事件保持严格迁移，启动器也不能把旧终态作为当前停止确认。
- 连接新后端身份需显式确认，不能据此认定旧作业已停止。
- `hello_ack.last_command_sequence` 同步后端命令水位，停止重试与其他控制器接管保持严格递增。
- Formal 本机进程监管保留后代身份及继承的运行标识，跨进程组或父进程退出不代替清理完成。
- 启动器停止未确认时持续监管并保留恢复身份，确认进程退出才删除；`stop-session` 只确认非运行，不确认进程退出。
- 全场功率要求同一步全部预期机组及合计有限；Interactive 与 Formal Training／SB3 分别核对。
- MPI 清除继承作业身份、保留显式传输配置，macOS 设置仅作缺省；后端修改后重启 Bridge。
- 项目启动器加载安装扩展；仓库 `scripts/blender/run_wfrl_macos.sh` 已包含 `launch`，直接运行 `scripts/blender/wfrl_launcher.py` 才加此子命令。
- 普通手动连接需要 `Load & Validate Scene`，启动器主动连接提供自动加载。

Replay、Interactive 与 Formal Training 的环境、动作及验收分别按[训练合同](../../../../docs/training/MAPPO_SETUP.md)选择；Bridge 模拟或 socketpair 检查不证明真实训练、停止监管或控制收益。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 实时场景归属与尾流有效性 | `test_live_scene_ownership.py`、`test_wake_payload.py` | 后台 `live_contracts_regression.py` 验证非活动场景及所属 View Layer、无消息时删除归属场景、显式重连、尾流过期与恢复、过期期间开关及逐 View Layer 隐藏保护；不启动真实后端。 |
| Bridge 与训练 | `test_transport.py`、`test_live_runtime.py`；仓库 `tests/blender_bridge/`、`tests/test_interactive_ppo_objective.py` | 回合、动作及训练路径按[训练合同](../../../../docs/training/MAPPO_SETUP.md)选择对应测试；模拟测试不代替真实连接／训练。 |
| 连接恢复与身份 | `test_connection_recovery.py`、`test_transport.py`、`test_ui_state.py`；仓库 `tests/blender_bridge/test_disconnect_progress.py` | 后台 `connection_recovery_regression.py` 核对真实 RNA／操作器确认及 socketpair 握手，不连接 TCP 服务、不运行后端，也不验证模态窗口布局。 |
| 快照与曲线回合、单位合同 | `test_chart_snapshot_contract.py`、`test_charts.py`、`test_history_export.py`；仓库 `tests/blender_bridge/test_messages.py` | 使用实际 `SnapshotAdapter` 到 `ChartHistory` 的生产与消费合同，校验 rpm／deg／MW 且拒绝单位不符的有效或无效记录；不启动训练或验证控制效果。 |
| 启动器与 MPI | `test_launcher.py`；仓库 `tests/blender_bridge/test_isolated_trainer.py` | MPI／FAST.Farm 须目标环境验证。 |
| 雷达与便携包 | `test_dual_beam_builtin.py`；仓库 `tests/lidar/` 的 `test_replay.py`、`test_dual_beam_replay.py`、`test_dual_beam_portable.py` | 后台 `dual_beam_regression.py`、`clearance_replay_smoke.py`；窗口 `dual_beam_window.py`、`clearance_gui_acceptance.py`。 |
