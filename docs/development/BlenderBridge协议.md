# Blender Bridge 会话、消息与停止合同

本文描述工作区 Python 后端的当前行为。规范消息定义在 [messages.py](../../wfrl/blender_bridge/messages.py)，发送实现位于 [server.py](../../wfrl/blender_bridge/server.py)，作业归属由 [BackendSession](../../wfrl/blender_bridge/backend_session.py) 管理。协议版本为 `1`，服务器只绑定 loopback IP；这些源码行为不自动代表公开扩展或本机安装载荷，安装身份见[文档索引](../README.md)。

## 消息队列与连接恢复

`snapshot` 与 `training_stats` 是可合并的观察数据；`lifecycle`、`safety_event`、`error` 是可靠事件，使用独立的有界会话队列。可靠事件优先于尚未发送的观察，暂时没有发送空间时观察进入延后槽并保留原等待年龄。已经部分发送的帧不能替换。`training_stats` 需要协商 `training_stats_v1` 能力；断连恢复不承诺补齐全部观察历史。

`BridgeServer.poll()` 在断连或握手等待期间仍调用会话 `tick()`，推进任务完成、失败、进度读取及资源清理。会话可靠队列真正溢出时报告 `EVENT_OVERFLOW`，将任务置为 `FAILED` 并请求停止；这与单帧无法编码的处理不同。

### fatal 后的连接收尾

`error.fatal=True` 入发送队列后，当前连接进入 `close_after_flush`：清空延后的观察，不再接纳新的 `snapshot` 或 `training_stats`，排完已排队数据后断开。后台生命周期仍继续推进。可编码的可靠事件保留在会话队列或服务器 backlog，在同一会话恢复连接后补发；fatal 不构成物理后端已停止或已清理的证明。

### 超限帧与诊断

超过协议 `MAX_FRAME_BYTES` 上限的帧，以及单帧已超过发送预算的可靠事件，无法靠等待腾出空间解决，会被丢弃，不永久占据 backlog 阻塞后续消息。普通观察仅因队列暂时没有空间时仍可延后。

服务器尝试发送非致命 `FRAME_TOO_LARGE` 诊断；诊断本身也必须放得下。发送暂时受阻但能保留时延后补发，诊断本身超限时不递归产生诊断。按消息类型与 `run_id` 去重，只在诊断实际入队或被保留后记为已报告。可靠队列不能据名称理解为对不可编码帧也无条件保留。

### safety_event 的等待年龄

`safety_event.data.source_age_seconds` 在原有年龄上累计会话队列、发送队列及断线等待时间。会话出队和发送端使用各自的单调时钟等待量；发送前以原始年龄基准重算，重试不会反复叠加同一段等待。帧开始发送后保持字节不变，因此年龄固定在该帧开始发送时；断连重发保留原时间基准并重新计算等待年龄，不将事件伪装成刚产生的数据。

对应合同用例为 [队列故障与年龄](../../tests/blender_bridge/test_bridge_queue_faults.py)、[发送背压](../../tests/blender_bridge/test_server_backpressure.py)及[断连进度](../../tests/blender_bridge/test_disconnect_progress.py)。这些是代码核对入口，不表示当前环境已经运行过相应测试。

## 正式训练的停止与清理

Formal Training 运行独立的 `scripts/train/train_fastfarm.py`。其协作式中断在完整更新边界保存检查点，`save_interval=0` 仍保存；恢复前校验软件状态及进度计数，见[正式 CLI 的中断与恢复](../training/MAPPO_SETUP.md#正式-cli-的中断与恢复)。该模式不支持 Pause、Resume 或 Step，也不提供逐步三维遥测。

### POSIX 信号与进程归属

[FormalProcesses](../../wfrl/blender_bridge/formal_process.py) 按 PID、创建时间、已发现的后代及继承的运行标识维护本机进程归属。协作式停止只向精确匹配命令行的唯一训练主进程发送 SIGINT，不向 MPI launcher、求解器或整个进程组广播 SIGINT。无法确定唯一主进程时报告请求失败，不猜测接收者。

`BackendSession.stop()` 默认等待 120 秒；超时或中断请求失败后，对所属进程树执行 SIGTERM，仍未退出则执行 SIGKILL，并继续确认清理。父进程退出、子进程更换进程组或脱离父进程，不单独构成清理完成证明。超时必须有限、非负且不超过 `threading.TIMEOUT_MAX`。

### 停止结果与清理确认

停止前、等待结束后及最终清理时都复核错误与退出码。任务结果和资源清理是两个维度：

| 观察结果 | 当前判定 |
| --- | --- |
| 协作式停止后退出码为 0，且无其他失败 | 可进入 `STOPPED`；仍须单独等待清理确认。 |
| POSIX 上已成功请求本次中断，随后退出码为 `-SIGINT` 或 `128+SIGINT` | 作为预期取消处理；没有该请求记录时，同样的非零退出码属于失败。 |
| 其他非零退出码，或已有后端／关闭错误 | 保持 `FAILED`，附加原因，不因 Stop 转成正常停止。 |
| 超时或请求失败，所属进程已被强制终止 | `FAILED`，注明“应用层收尾未确认”；进程资源仍可能完成独立清理确认。 |
| 所属进程仍存活、身份不确定，或进度读取器／临时资源仍未释放 | `cleanup_confirmed=False`；禁止新启动、复位或重配。 |

`cleanup_confirmed=True` 要求状态为 `READY`、`STOPPED` 或 `FAILED`，无存活任务、停止线程结束、进度读取器和所属临时资源全部释放。`FAILED` 可以与已确认清理同时出现；仅看到退出码、`STOPPED` 或 `FAILED` 都不能替代这组条件。排空期间只接受 `run.stop` 与 `session.sync`。

### Windows 控制台与验证范围

Windows 以 `CREATE_NEW_PROCESS_GROUP` 启动正式进程，使用 `CTRL_BREAK_EVENT` 请求应用层停止，需要可用的共享控制台。发送失败时直接进入终止流程，不继续等待完整协作式超时；状态保留 CTRL_BREAK 失败原因与“应用层收尾未确认”。Windows 的强制信号分支使用 SIGTERM 终止所属进程，再核对实际退出与清理状态。

**Windows 实机尚未验证。** SIGBREAK 注册和 CTRL_BREAK 故障替身用例只描述软件分支，不能证明真实 Windows 控制台、MS-MPI、FAST.Farm 进程树或检查点收尾已验收。依据为 [正式停止故障用例](../../tests/blender_bridge/test_formal_shutdown_faults.py)、[进程归属用例](../../tests/blender_bridge/test_formal_process_ownership.py)及[清理确认用例](../../tests/blender_bridge/test_cleanup_confirmation.py)。

## 测量与安全事件的边界

海上场景保留 `scene.substructure` 请求；快照 `hydrodynamics` 区分 `not_computed`、`requested`、`configured` 与 `computed`。只有真实模式下各机七个海上通道均有效且物理身份一致，才声明 `computed`；视觉单桩、场景请求或输入文件本身不能代替该状态。泥线载荷、过渡段运动和波面通道的单位、来源与主机验证范围见 [OC3 海上遥测合同](OC3海上单桩与波浪.md#输出与-blender-主机合同)。

Driver 的缺测、角度单位、MPI 交换预算与算例保留见 [FastFarmDriver 测量与交换预算](../training/MAPPO_SETUP.md#fastfarmdriver-测量与交换预算)。`SafetyLimiter.monitor()` 的超速事件只进入快照和事件记录；`block` 严重级别不触发训练停止，也不改变动作或奖励。正式 CLI 采样不调用该监测，峰值读取也不按控制窗时间下界过滤，详见[安全监测的范围](../training/MAPPO_SETUP.md#安全监测的范围)。
