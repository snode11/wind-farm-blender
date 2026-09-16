# MAPPO 60 秒演示 · 0.3.0

## 从项目启动（MAPPO 默认回放，后端待用）

```bash
cd "/Users/eason/Desktop/wfcrl/wind farm RL"
scripts/blender/run_wfrl_macos.sh \
  --blender "/Applications/Blender.app/Contents/MacOS/Blender" \
  --python /opt/anaconda3/envs/wfrl-mac/bin/python \
  --scene scenes/turb3_row.yaml \
  --mpi /opt/homebrew/bin/mpiexec \
  --fastfarm /opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm
```

该命令加载本机已安装的 0.3.0，默认打开 60 秒 MAPPO 片段，停在 T1 侧前方首帧；在右侧 **MAPPO → 三机 MAPPO 回放** 点击播放。Bridge 同时在 `127.0.0.1:8765` 待用，默认不连接、不启动求解或训练。

需要实时后端时，在右侧 **Item → WFRL / CONNECTION → 后端连接（高级）** 选择 **Replay**（已有权重推理）或 **Interactive**（训练），再点 **Connect / Reconnect**。如果未显示 Item 标签，先在画面中点选一片叶片。确认握手后，启动器自动加载命令指定的 `scenes/turb3_row.yaml`；界面显示 **CONNECTED / READY** 后再配置并启动运行。Replay 需要在“后端运行”的 Checkpoint 中选择匹配权重，例如 `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt`。切换后端模式本身不启动运行。

结束后端运行时，先点 Stop 并等待 **STOPPED**，再点 **MAPPO · 60 秒** 返回离线回放。离线状态显示 **OFFLINE RESULTS**，这表示当前查看已保存结果；不能把它当成后端连接失败。关闭由命令启动的 Blender 后，启动器清理其拥有的 Bridge。重复运行同一命令前需先退出上一窗口，避免 8765 端口占用。

实时 `turb3_row.yaml` 工况是 8 m/s；随包 60 秒片段是此前保存的随机阵风工况，两者数据来源独立。实时转速通道当前按功率/转矩公式推算，并标记 `SYNTH` 与公式来源；离线片段使用保存的转速记录。实时后端不要选择旧的 `Start Backend Demo` 合成示意来验证 FAST.Farm。

## 便携入口及回放操作

Mac 双击“打开三机叶片回放.command”。需要 Blender 5.2 或更新版本。
右侧 N 侧栏选择 **MAPPO → 三机 MAPPO 回放**。安装 ZIP 后，也可以在同一面板点击“加载 MAPPO · 60 秒”。
扩展 ZIP 内置完整三机数据、读取器和界面；便携目录可以整体移动，离线播放无需仓库、训练环境、FAST.Farm 或网络。

- 播放/暂停、从头重播和进度滑块使用同一条 60 秒时间轴。单步推进 1/60 仿真秒，停止保留当前画面，复位回到起点并暂停，片尾保持最终记录状态。
- T1/T2/T3 Down、T1 侧前方、风场全景；更多视角内有测量区、雷达特写和云台 Down/Back、拖动、变焦。切镜头保留时间与暂停状态。
- 三机九片叶片独立形变；叶尖轨迹 B1 橙、B2 蓝、B3 红，最多 540 点。同机镜头切换保留轨迹，换机、寻址、重播清空并重画。
- “三机遥测、曲线与导出”：偏航、平均变桨、转速、发电功率、转矩、B1 叶根弯矩，以及风场总功率。曲线与历史每通道最多 600 点，关闭采样时冻结。JSON 导出包含来源与单位。
- “视图、环境与截图录制”：Top/Side、风场+机舱双视图、日间/阴天/黄昏、显示质量、尾流示意、演示/编辑模式、截图、窗口 PNG 序列、静帧与动画渲染。
- 雷达保留同源 B2 测距、叶尖—塔筒净空真值/估计/误差和累计统计；无效或过期显示缺失，不用其他光束补齐。

当前 Demo 已移除旧 66 秒 SYNTH 时间线、虚构功率、启动/顺桨脚本事件、故障展示夹具和手动改写物理姿态入口。停止不会把真实录制片尾改成零转速或虚构顺桨。后端连接、训练和独立雷达包工具仍可使用。

三机物理记录仍为 REVIEW_ONLY。MAPPO 控偏航，降额基线控制器负责转矩/变桨；没有重跑仿真或重新训练。功率等新增遥测从同一次已保存 OpenFAST 输出中提取，经时间、偏航、转速和变桨核对，并随包记录源文件 SHA-256。奖励未记录；地形、尾流线和雷达光束为显示示意。未证明现场精度、控制收益、数值收敛或稳定 60 FPS。

便携启动器使用随包扩展；本机已安装扩展也已同步。此前打开的 Blender 进程需重新启动。
