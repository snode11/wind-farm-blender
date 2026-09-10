# 前后端联动验收 — 2026-09-08

结论：运行链路通过；正常停止未通过，暂不作为完整验收通过。

环境：Blender 5.2.1 LTS 真实 GUI、Bridge/MPI、原生 FAST.Farm，
交互训练 turb3_ctrl3.yaml（yaw/pitch/torque），不是 SYNTH 或回放。
参数：iters=100、n_steps=8、warmup_steps=0；在启动训练 180.04 秒后主动停止。
整个监督进程历时 303.14 秒，未达到 10 分钟限制。

## 已验证

- 前端连接真实后端，记录 91 组不同步数样本（第 2 至 92 步）。
- 样本包含 sampling 和 updating，确认执行了策略更新。
- T1 实测变桨 0–1.7381°，前端叶片角与采样值最大误差 0°。
- T1 转速 8.8261–9.4701 rpm，91 组样本的转子角均不同。
- T1 偏航 1.7216–6.0164°，功率 1.6203–1.7597 MW，相关通道均 valid。
- 场景对象数始终 2647；此记录不等同于内存或持续帧率基准。
- World、Top、Side、T1.Closeup 四个镜头切换成功。
- 原生窗口截图成功，并通过实时窗口观察近景及 RUNNING/CONNECTED 状态。
- 原生历史导出成功，JSON 可读取：41 组序列、3854 条记录。

## 未通过和限制

1. 正常停止：180 秒发出 run.stop 后处于 DRAINING，约 120 秒后变为 FAILED。
   Trainer.stop 等待线程结束；底层 close(purge=True) 排空预设 FAST.Farm 预算。
   本次启动预算为 842 次通信迭代，停止时仅推进至第 92 步，因此排空明显超时。
   最终监督程序向本次拥有的 MPI 进程组发出终止信号进行清理。
   进程检查确认无遗留 FAST.Farm 或本次 Bridge。未修改停止实现。
2. 首次生成场景期间约 50 秒后才记录到前端样本；应进一步拆分建模、着色器和初始化耗时。
3. FAST.Farm 日志存在尾流平面超出低分辨率计算域 X 边界的警告。
   本次数据继续更新，但不据此判定仿真域配置及数值精度合格。
4. 未覆盖暂停/单步/重连、多窗口布局或长时间内存稳定性。

证据：report.json、history.json、backend.log、blender.log、supervision.json。
blender_run.py 和 supervise.py 为本次可复现的验收脚本。
