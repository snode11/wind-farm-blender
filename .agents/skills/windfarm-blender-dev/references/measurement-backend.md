# 测量、后端与协议

用于 X100 点云与采集、理想激光／双束净空与报警、快照曲线、DisXY、Blender Bridge、连接恢复及启动器任务。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| X100 点云、几何仿真、文件回放与本地采集 | `radar_x100/`、`panels/radar_x100.py`；仓库 `wfrl/radar_x100/`、`scripts/radar_x100/` | [操作与验证范围](../../../../docs/blender/X100雷达.md)、[规范数据与采集合同](../../../../wfrl/radar_x100/README.md) |
| X100 独立点云窗口 | `radar_x100/output.py`、`radar_x100/display.py`；共用场景叠加的属性着色 | [观察窗口](../../../../docs/blender/X100雷达.md#独立点云输出窗口)、[检查参数与完成条件](verification.md#执行环境与来源) |
| X100 面积材料点、安装搜索与叶片估计 | 仓库 `wfrl/radar_x100/sampling.py`、`installation.py`、`tracking.py`、`clearance.py` 及 `scripts/radar_x100/`；Blender `radar_x100/scene.py`、`radar_x100/estimation.py`、`radar_x100/clearance.py`、`radar_x100/output.py` | [研究操作与允许先验](../../../../docs/blender/X100观测研究.md)、[研究检查](verification.md#x100-观测研究)；工作区能力，公开包和日常安装独立识别 |
| X100 会话、观测与记录 | `radar_x100/runtime.py` 管会话生命周期；`radar_x100/observations.py` 管采样与显示，`radar_x100/mount_presets.py` 管安装事务，`radar_x100/session_recording.py` 管记录及索引回放 | `runtime.Session` 与 `runtime.apply_mount_preset()` 保留调用入口，派生分析／估计由 `radar_x100/estimation.py` 实现 |
| 理想激光／双束净空、报警与计算 | `clearance_replay.py`、`clearance_visual.py`、`radar_feedback.py`；仓库 `wfrl/lidar/`、`scripts/lidar/` | [结果合同](../../../../wfrl/lidar/REPLAY_FORMAT.md)、[算法](../../../../wfrl/lidar/README.md)、[计算流程](../../../../scripts/lidar/README.md) |
| 后端连接与训练 | `runtime.py`、`transport.py`、`panels/run.py`；仓库 `wfrl/blender_bridge/` | [训练入口与合同](../../../../docs/training/MAPPO_SETUP.md) |

## X100 来源与采集

- X100 独立于理想激光、双束净空及训练 Bridge。`x100-frame.v1` 保留传感器／会话／流／序号、`source_kind` 和时钟域；文件回放不改变原始来源，几何仿真不作为实测。仓库 `simulation.simulate_detections()` 与 `detection` 提供 `ENGINEERING_SIMULATION / DETECTIONS`，默认 UNCALIBRATED；工作区 `wfrl_x100_observation_mode` 默认GEOMETRY调用 `simulation.simulate()`，显式应用DETECTIONS调用 `simulate_detections()`；工程帧无material_ids，界面显示模式、标定、检测数、虚警数及可选模型SNR。参考面积积分预算与最多4096输出点独立，模型v2使用77 GHz、效率各0.7的归一化方向图增益、kT0·B·NF噪声和假设锚点反推的43.1 dB处理增益；0.2 m／1°／2°／0.1 m/s四维聚类及高斯误差均保留来源／假设。手册未给参考RCS与Pd标定指标；厂家mag／rcs／snr仍未知，模型量只作独立诊断和显示。当前构建清单包含 `detection.py` 与 `clearance.py`，安装验证入口覆盖检测模式与净空，实际载荷结论独立核对；假设与参数按[工程模型](../../../../wfrl/radar_x100/README.md#工程检测模型)核对。
- 工作区入口为独立侧栏 **N → X100**，通过 `panels/home.py` 的 `busy_reason` 共用相机与录制占用判断；公开包和日常安装的入口按各自载荷识别，不由源码推定。
- 几何仿真要求所属场景已加载同源 FarmFlex；`SceneSampler.sample(t)` 只读保存运动，不推进场景或播放器。30 Hz 是声明的源采样网格，不保证 UI 记录覆盖完整网格或持续 30 Hz 绘制；完整网格导出按[几何观测入口](../../../../wfrl/radar_x100/README.md#几何观测)使用独立进程和新输出。安装或来源不匹配时撤回点云并保留用户修改，不运行求解或训练。

- 同一台几何雷达的原安装位／机舱侧面／T1 Down 相机位（`DOWN_CAMERA`，取相机位姿、半角与 `PROJECTIVE` 视场形状）／尾部吊物口（`REAR_HATCH`，`scene.rear_hatch_installation()`，视轴对准 6 点钟叶尖）通过 `runtime.apply_mount_preset()` 切换，保留场景时刻；已应用视场 `wfrl_x100_field_of_view` 与视场形状 `wfrl_x100_field_of_view_shape` 属于待应用配置；记录、相机采集与共享安装按其互斥约束拒绝切换。安装对象及父级不支持约束、动画或非对象父级关系，失效时撤回观测，不覆盖原生编辑。
- `SceneSampler.material_sample(t, triangle_ids, barycentric=...)` 为面积材料差分只采样输出点，当前完整几何用于遮挡；静态拓扑与同刻遮挡树按绑定复用，来源和材料校验保持。性能结论须保留相同输入、点数组、材料身份与速度的对照；CPU 更新耗时不能换算成未经测量的播放 FPS。
- PLY 单帧不含连续时间、速度或航迹身份；序列按[显式索引合同](../../../../wfrl/radar_x100/README.md#ply-与显式索引)导入，不按文件名补时间或补帧。`FPS_DERIVED` 不代表硬件同步；未确认的实测坐标保留 `VENDOR_RAW / UNALIGNED`，不绑定不相关的 MAPPO 动画。
- 已应用回放路径与草稿独立保存。单帧 PLY 和记录目录的 `//` 读取以当前 `.blend` 所在目录为基准，已保存的绝对路径仍指向原位置。
- 工作区的单帧 PLY 要求显式 `source_kind`，`source_declaration=EXPLICIT` 仅表示调用方声明；路径改变会清除界面声明，路径或声明变更后须重新应用配置。完整记录保留文件内来源。几何采样同时校验机舱静态网格、变换及归属，编辑或驱动导致绑定失效时撤回观测；使用修改后的静态外壳须重新绑定，不自动烘焙驱动。
- 新记录目录必须不存在，至少写入一帧且成功关闭才完整；零帧失败，合法零点帧仍是有效观测。关闭期间记录器为 `FINALIZING`，最终清单写入并删除 `INCOMPLETE` 后才为 `COMPLETE`；场景与面板在写盘线程退出前显示 `FINALIZING`。记录追加或关闭异常、预算或队列失败均使记录进入 `FAILED`，保留错误及 `INCOMPLETE`，不能作为完整回放。正常关闭排空已接受的记录；失败后释放待处理项，不补认成功。显示提交保存帧快照，写盘前重新校验元数据；显示最新槽与记录队列分开，迟到前帧可进入记录但不替换较新显示，Blender 最新帧不作为完整采集记录。
- 分片在达到超时边界时即失效；组帧时间须有限，队列容量须为范围内整数。帧必须声明支持的 schema；本地通道拒绝重复 JSON 键、非有限元数据及错误的身份／字段类型，断连残包报截断。PLY 头含 `end_header` 行在内不得超过16384字节，点布局与载荷长度须匹配。发布重试重发完整帧并保留原到达年龄，关闭须中断阻塞发送并确认线程退出。
- 工作区结束记录先拒绝新的记录提交，立即返回，由轮询确认线程退出后再释放；等待期间保留归属并拒绝重启或替换会话。加载、历史恢复和卸载同样保留写盘收尾轮询，所属场景失效时跳过状态写入。真正的关闭失败保留原因。仓库 `scripts/radar_x100/serve.py --record` 等待并核对最终记录状态，零帧或记录失败返回非零退出码。

- Blender 的最后记录状态、原因、目录与已写帧数属于场景，独立于活动写盘器；结束失败记录后仍显示 FAILED，开始新的记录才清除上次错误。场景删除时跳过无效 RNA 的状态写入，仍关闭记录线程并释放会话。
- 实时输入只连接独立采集进程的 `127.0.0.1` 通道，各流身份与新鲜度独立；有效空帧清空点云，过期或断开隐藏旧点，新会话须显式重新连接。完整记录由外部采集端执行；厂家 UDP／SDK 解码、Track 航迹、实机标定及雷达与相机的投影／时间同步尚未接入，软件注入或文件转发不证明设备兼容、厂家精度或安全保护。
- GPU 点云与视场注释不进入原始 RGB，实体外壳保留。相机采集期间拒绝应用／移除安装，所属场景的几何仿真保留采集前观测，实时接收仍继续；混合任务同时读取[相机、资源与重建](cameras-reconstruction.md)，后台状态、窗口／GPU 像素与实机分别验证。
- 独立点云窗口只观察所属场景会话的最新帧，不创建相机、不采样、不推进播放或累积历史。投影、缩放、平移和着色只改变观察；关闭窗口保留会话，文件加载、撤销／重做和卸载按窗口归属释放资源，不影响其他 Image Editor。

- 动静属性使用固定 0／1 颜色，速度支持固定对称与逐帧范围；端点饱和只影响颜色，未知量仍为灰色。窗口色标选择不改变三维叠加、观测或记录；窄窗按可用宽高安排字号、按钮行和刻度，分别核对数据合同、实际 GPU 布局和真实输入。

### 同帧机舱分析与离线覆盖

- 工作区机舱 XZ 使用完整同刻机舱姿态，局部 Z 不保证重力竖直；侧壁距离为无符号三维诊断，目标未关联。原安装／侧面是同一雷达的互斥预设，不推定双雷达并发。
- 侧壁身份按构造多边形与三角化映射核对，排除端盖；保持 tower_mesh 二元组、FarmFlex 塔五元组及刚塔回退。纯 NumPy 基准独立于 Blender 的共同锚点 BVH 和选中三角面的 float64 精化。
- 原帧与只读上下文按运行代／会话／来源／时钟／帧／回放索引配对。窗口主线程轮询派生，draw/consume 不采样、不建立或查询 BVH。ROI 变化只重算当前冻结快照；原始索引保留，灰色未知不补零，不借塔距分类或删除点。
- 分析、普通 GPU 和录制异常不撤回合法原帧；真正来源失效按来源路径处理。预设切换必需 GPU 准备失败仍回滚，可选分析失败单独标识。分析上下文不进入 provenance 或记录，旧记录无上下文时仅作原始显示。
- 同帧距离分析只缓存成功结果；相同失败请求至少间隔0.25秒重试，ROI改变立即重算。新帧的采样事务负责一次完整来源校验，暂停、相机采集期间的轮询及独立参考计算继续各自校验，不跨tick跳过原生编辑检测。
- `scripts/radar_x100/coverage.py` 显式枚举完整30 Hz网格，评价原位、前部下视、侧面与尾部下视；报告几何名义下方经过的 N、E/V/O、条件分母、分箱、连续段、缺口和 UNKNOWN，并独立报告机械筛选及全源网格模拟检测支持。有限 V 不是 O 的严格上界。软件 ROI 为同刻机舱坐标，不改变硬件范围或检测积分域；参考材料真值只在几何评价端使用，零分母 N/A，参数是研究配置。功能及当前证据按[X100 说明](../../../../docs/blender/X100雷达.md)与[验证范围](../../../../docs/blender/发布状态与验证范围.md#x100-工作区源码验证)核对，不作为实机精度、叶尖识别或雷视融合验收。

### 安装搜索与叶片观测研究

- 安装搜索将每个完成的候选原子写入任务目录的独立 JSON，在内存中保留排序字段，按排名逐候选读取检查点并写出完整证据。失败或中断保留候选与元数据。Blender 接受 16 MiB 以内的完整报告或摘要，已有兼容完整报告可用 `scripts/radar_x100/extract_mount_summary.py` 提取。[搜索与摘要提取](../../../../docs/blender/X100观测研究.md#安装覆盖搜索)

- 工作区 `sampling.py` 以参考表面积和随机平移 Halton 序列固定材料坐标，算法身份为 `shifted-halton.v1`；计划身份、算法、三角面及点索引进入来源信息，面积模式小预算保持大预算前缀。面积采样、安装搜索与叶片估计由 0.3.42.8 扩展及配套源码提供，日常安装按归档摘要独立核对，验证范围见[安装记录](../../../../publication/daily-installation.json)。
- 面积计划包含 `area_weights_m2`，`sampling_plan_id` 同时绑定参考面积度量与权重摘要；坐标前缀相同不表示权重和计划身份相同。工程检测不携带一一材料点身份。
- `scripts/radar_x100/optimize_mount.py` 输出v2报告，机械／运动研究通过后，以完整叶片经过的末段ROI检测、最长连续漏检、精确正下方支持和塔距误差为主排序，几何覆盖仅作辅助。排序和导入共用指标校验：缺失、非有限、越界或未知检测指标不计分；候选ID须唯一，来源、几何、位姿及筛选范围不符时拒绝并指明字段。导入还要求当前安装包络和机械筛选策略，缺失或不符时须重跑搜索。闭合网格包含判定包含边界点，开放或绕序不一致的网格拒绝；未知运动净空不算通过。仅筛选前若干经过不提供可应用winner。ROI可能包含塔架返回，支持率不是叶片识别率。默认GEOMETRY，连续检测支持未建立时不推荐DETECTIONS。v1几何报告不支持检测排名，PASS_RESEARCH不等于实体安装批准。[有限候选比较与本地证据](../../../../docs/blender/X100观测研究.md#有限候选比较与本地证据)
- `tracking.py` 只消费观测、同帧塔面和声明的根部姿态／名义叶尖径向坐标；Blender 先验以轮毂为原点、末端局部Z取 `TipRad`。目标走廊关联、背景排除、分段中位数和稳健二次拟合只影响派生结果，不修改原点云或侧壁诊断；禁止消费材料标签或变形叶尖。局部因果平滑不引入旋转滞后，缺测、换源、后退和长间隔不输出旧估计。 连续有效估计次数按配置的最大间隔计算，源网格的覆盖率由离线研究独立计算。工作区对合法暂停帧的 GPU 叠加重建和同帧临时估计异常限频重试。
- DETECTIONS 使用 `clearance.ClearanceTracker.update()` 的因果净空估计，另需径向速度、位置误差模型和未加载叶片形状先验；有诊断时仅消费观测总功率／噪声比，不消费纯信号功率、虚警真值或材料标签。退化拟合、无效塔面查询及过大不确定度不出数；DETECTIONS 的派生显示可另存逐帧 sidecar，回放核对摘要后显示，不用当前场景重新推理。
- 离线 `clearance.track_clearance_passage()` 在每片叶片的同一经过内运行局部 Kalman／RTS 平滑；保留受支持的单帧值，缺测结果统一标“跟踪外推”／`TRACKING_EXTRAPOLATED`，包括使用未来锚点的插值。默认局部窗0.20 s、至少3个锚点，距最近锚点超过0.20 s或净空标准差超过0.45 m时不出数；时间后退／重复或间隔超过0.12 s时分段。该离线入口不改变前端因果更新，也不构成新观测或实机置信度。
- 估计由会话主线程按新配对帧派生，不依赖观察窗口；开关关闭清除结果和历史。画布仅消费已有结果，辅助顶点按C连续 `float32` 提交，拟合线／叶尖在原始点云之后按画布裁剪绘制；窗口像素、后台合同和实机精度分别判定。
- 拟合残差、离散度和几何塔距不代表实机精度或安全阈值。`evaluate_blades.py` 在推理后用独立保存的结构叶尖与材料身份评价，完整源网格、参数及未知项写入研究结果。操作与当前限制见[X100观测研究](../../../../docs/blender/X100观测研究.md)。

## 理想激光与双束统计

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
- DisXY `shape` 各维须为正整数（接受整数值浮点数），小数、字符串与布尔值拒绝而不截断，由 `test_wake_payload.py` 覆盖。
- DisXY 材质加载失败不阻断网格创建与后续更新，错误写入运行状态 `error`，不改变连接或尾流有效性；同类失败只打印一次 traceback，由 `test_wake_payload.py` 覆盖。

字段、单位及跨层转换以仓库 `wfrl/blender_bridge/messages.py`、[结果合同](../../../../wfrl/lidar/REPLAY_FORMAT.md)及相应生产／消费测试为准；坐标与变换按项目接口核对。外部风场模型的定义不能直接作为本项目接口约定。

## 实时场景归属

- 实时后端绑定连接时的所属场景；快照、姿态、来流及尾流不随活动场景迁移，非活动场景构建须使用所属 Scene／View Layer 上下文。
- 每次轮询前核对归属，所属场景删除时即使没有消息也断开并保留未确认状态，不自动认领另一场景；显式重连才可绑定当前非手动场景，保留会话身份与命令水位。

## 连接、作业与命令身份

- `BridgeServer._accept_client()` 在活动连接尚未断开时保留一个等待连接，不读取等待方的 hello；旧连接读到 EOF 或出错后才转入握手。等待和握手共用从 accept 起计算的 `handshake_timeout`，默认5秒，须为有限非负数；旧连接持续存活时只关闭到期等待方。旧连接残留命令照常经过 `SessionSequenceGuard.accept()`，恢复确认携带处理后的命令水位，不因重连跳过序号校验。
- `BridgeServer.poll()` 无客户端仍推进；快照／统计与可靠生命周期／安全／错误事件分队列。可靠事件优先于尚未发送的观察数据，发送预算不足时延后快照及训练统计，保留原等待年龄；部分发送的帧不可替换，观察消息因年龄字段增长而超预算时也延后。可靠事件队列真实溢出才报 `EVENT_OVERFLOW` 并失败停止，重连不保证完整观察历史。
- `BackendSession` 在停止前或停止中发现后端错误时保持 `FAILED`，清理异常／超时附加到主错误；同步快照回调失败不得再进入 RUNNING。清理确认还要求进度读取器及所属临时资源释放，确认前拒绝启动、复位和重配；排空期间仅接受停止与同步，通道修改也拒绝。`stop()` 超时须为有限非负数且不超过 `threading.TIMEOUT_MAX`。服务器关闭即使停止抛错也释放监听套接字。
- `state_revision` 握手覆盖的生命周期只进入诊断，不回退当前状态；新事件保持严格迁移，启动器也不能把旧终态作为当前停止确认。
- 连接新后端身份需显式确认，不能据此认定旧作业已停止。
- `hello_ack.last_command_sequence` 同步后端命令水位，停止重试与其他控制器接管保持严格递增。 工作区 Bridge 的非致命 `COMMAND_REJECTED` 携带 `command_sequence`；仅匹配当前待确认命令的拒绝可解除等待，迟到的旧拒绝不影响新命令。待确认期间收到无序号拒绝时断开并要求重新同步。
- Formal 本机进程监管保留后代身份及继承的运行标识，跨进程组或父进程退出不代替清理完成。
- 启动器按 PID／创建时间和运行标识监管全部本机所属进程，父进程退出或身份读取失败不证明清理完成；只有 EMPTY_CONFIRMED 才删除归属与 Bridge 会话记录。Bridge 独立声明固定端点、活进程和当前协议会话，bootstrap 不写停止凭据；界面换连接不改变启动器归属。`stop-session` 核对这些身份后只确认非运行，不确认进程退出。Windows 上启动器以 `CREATE_NEW_PROCESS_GROUP` 启动并用 `signal.CTRL_BREAK_EVENT` 请求停止，须共享控制台；前后端联合验收在 Windows 上以 `EMPTY_CONFIRMED` 而非 Bridge 退出码判定清理。
- 全场功率要求同一步全部预期机组及合计有限；Interactive 与 Formal Training／SB3 分别核对。
- MPI 清除继承作业身份、保留显式传输配置，macOS 设置仅作缺省；后端修改后重启 Bridge。
- 项目启动器加载安装扩展；仓库 `scripts/blender/run_wfrl_macos.sh` 已包含 `launch`，直接运行 `scripts/blender/wfrl_launcher.py` 才加此子命令。
- 普通手动连接需要 `Load & Validate Scene`，启动器主动连接提供自动加载。
- 仓库 `wfrl/blender_bridge/__main__.py` 在平台提供时注册 SIGBREAK、SIGTERM、SIGHUP；信号请求停止一次，继续轮询直到清理确认且 worker 不再存活，再关闭并等待停止线程。关闭等待期间只接受停止与同步命令；Windows 控制台和进程清理仍需目标平台验证。

## FAST.Farm 风况

仓库 `wfrl/fastfarm_driver.py` 与 `wfcrl-env/wfcrl/interface.py` 保存请求、实际输入和采样统计三层 `wind_conditions`。270°为固定坐标匹配，不作旋转；BTS拒绝标量风速，其他未支持请求到达接口时抛 `WindRequestError`。BTS时长不足只记录警告，场内独立入流统计为 unavailable。`upstream_request.scene` 保留显示方向（display_only）及BTS覆盖的场景风速，与实际求解 `requested` 分开；训练 `--wind-direction` 经配置、调度、采样器及场景入口完整传到底层，成功与拒绝都保存上层上下文。具体见[风况合同](../../../../docs/training/FASTFarm风况.md)；不能把文件统计或受尾流影响的轮毂统计写成自由入流验证。

Replay、Interactive 与 Formal Training 的环境、动作及验收分别按[训练合同](../../../../docs/training/MAPPO_SETUP.md)选择；Bridge 模拟或 socketpair 检查不证明真实训练、停止监管或控制收益。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 实时场景归属与尾流有效性 | `test_live_scene_ownership.py`、`test_wake_payload.py` | 后台 `live_contracts_regression.py` 验证非活动场景及所属 View Layer、无消息时删除归属场景、显式重连、尾流过期与恢复、过期期间开关及逐 View Layer 隐藏保护；不启动真实后端。 |
| Bridge 与训练 | `test_transport.py`、`test_live_runtime.py`；仓库 `tests/blender_bridge/`、`tests/test_interactive_ppo_objective.py` | 回合、动作及训练路径按[训练合同](../../../../docs/training/MAPPO_SETUP.md)选择对应测试；模拟测试不代替真实连接／训练。 |
| 连接恢复与身份 | `test_connection_recovery.py`、`test_transport.py`、`test_ui_state.py`；仓库 `tests/blender_bridge/test_disconnect_progress.py`、`tests/blender_bridge/test_reconnect.py`、`tests/blender_bridge/test_main_signals.py` | 宿主覆盖等待接管、共享时限、序号和信号关闭；后台 `connection_recovery_regression.py` 核对真实 RNA／操作器确认及 socketpair 握手，不连接 TCP 服务、不运行后端，也不验证模态窗口布局。 |
| 快照与曲线回合、单位合同 | `test_chart_snapshot_contract.py`、`test_charts.py`、`test_history_export.py`；仓库 `tests/blender_bridge/test_messages.py` | 使用实际 `SnapshotAdapter` 到 `ChartHistory` 的生产与消费合同，校验 rpm／deg／MW 且拒绝单位不符的有效或无效记录；不启动训练或验证控制效果。 |
| 启动器与 MPI | `test_launcher.py`；仓库 `tests/blender_bridge/test_isolated_trainer.py` | MPI／FAST.Farm 须目标环境验证。 |
| X100 数据、采集与场景生命周期 | 仓库 `tests/radar_x100/`：PLY、来源／时钟、组帧、记录完整性及本地通道 | 后台 `radar_x100_regression.py`、`radar_x100_edges.py`、`radar_x100_history_regression.py` 覆盖只读采样、材料子集／完整几何差分一致性、安装驱动、记录失败终态、场景删除、采集互斥、保存重开、撤销／重做及资源归属；双安装位另选 `radar_x100_mount_presets.py`，不证明 GPU 或设备通过。 |
| X100 原始 RGB 与相机隔离 | 按上文 X100 合同及相机专题选择受影响检查 | 窗口 `radar_x100_window.py` 核对辅助物更新前后真实 RGB 字节、状态、可见性、时钟、相机参数及实际采集作业；不证明持续 30 Hz、完整片段 FPS 或实机同步。模块、输出和完成条件见[验证入口](verification.md#执行环境与来源)。 |
| X100 独立观察与窗口生命周期 | `test_radar_x100_display.py`、`test_radar_x100_recording_ownership.py`，检查投影、色标、布局约束与关闭归属 | 窗口 `radar_x100_output_window.py` 核对实际绘制、消费帧身份、导航隔离、换源／空帧／停止、原始 RGB 与场景归属；`radar_x100_output_lifecycle.py` 仅源码，核对保存重开、其他编辑器及卸载释放。宿主检查不代替 GPU 绘制。 |
| X100工程检测与净空 | 仓库 `tests/radar_x100/test_calibration.py`（实测校准、视场半角与视场形状）、`test_detection_physics.py`、`test_detection.py`、`test_simulation_detection.py`、`test_clearance.py`、`test_clearance_edges.py`；宿主 `test_radar_x100_detections.py` | 后台 `radar_x100_detection_mode.py`、`radar_x100_clearance.py` 支持源码和指定安装模块，核对模式、记录回放及净空；安装模式须校验包内模块和数据来源，不证明GPU或设备精度。 |
| X100 面积采样、安装搜索与叶片估计 | 仓库 `tests/radar_x100/` 的 `test_sampling.py`、`test_installation.py`、`test_tracking.py` | 后台 `radar_x100_observations.py` 核对来源绑定、未知报告拒绝、材料标签隔离及状态撤回；窗口 `radar_x100_estimation_window.py` 读回拟合线／模型叶尖像素，核对开关撤回、恢复和原始帧保留。脚本控制不代替外部输入或实机精度验证。 |
| 理想激光／双束与便携包 | `test_dual_beam_builtin.py`；仓库 `tests/lidar/` 的 `test_replay.py`、`test_dual_beam_replay.py`、`test_dual_beam_portable.py` | 后台 `dual_beam_regression.py`、`clearance_replay_smoke.py`；窗口 `dual_beam_window.py`、`clearance_gui_acceptance.py`。 |
