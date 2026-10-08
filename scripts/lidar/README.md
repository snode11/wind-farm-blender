# 离线激光净空数据生产

## 当前双束数据与可携带包

适用对象为 NREL / WFRL **0.3.13**；GW184 **0.4.0** 是三相机与合成缺陷的独立刚性场景，不运行本页雷达后处理。两个产品的下载和说明见[项目首页](../../README.md)。

0.3.13 安装 ZIP 包含默认旧法与显式可选 TLS 双束回放；公开 `v0.3.13` 源码同步前端、算法、工具、测试及构建资源。日常观看直接用 MAPPO → 净空 → 双束净空 / S1，或选择 TLS 候选 / S1，无需重新后处理或求解。两种结果共享原 40 Hz 源，默认仍为 `hub-axis.v1`。

- `postprocess_dual_beam.py --source <完整v3源包> --calibration <已确认标定JSON> --output <新目录>`：按全部保存网格处理 S1 命中及 S2/S3 同刻同叶片重建，不启动 FAST.Farm。必须显式选择已确认标定；脚本默认的 diagnostic 标定不是当前内侧安装的替代。
- `package_dual_beam.py --package <双束结果目录> --output <新目录>`：生成可携带目录，包含全部源数据的 `source/`，并重新校验。两个输出都使用新目录，保留原始运行和既有结果。
- `audit_dual_beam_accuracy.py`、`audit_measurement_window.py`：分别用于误差诊断和保存网格/测量窗审计；按脚本参数和匹配源数据使用，不把软件 PASS 当成精度通过。

2026-10-02 的 TLS 第一阶段增加显式 `postprocess_dual_beam.py --method hub-tls.v1`；未覆盖方法时保留标定中声明的方法，历史标定缺省仍用 `hub-axis.v1`。每个新结果保存实际生效配置和独立版本；旧法继续作为默认。`compare_dual_beam_methods.py` 重算公共/自身有效集合、全固定窗口覆盖与经过最低净空；`verify_dual_beam_isolation.py` 检查全网格真值删除/污染和独立 eigh 求解；`stress_dual_beam_pointing.py` 在固定窗口内改变指向后重新射线求交。开发压力结果与名义回放单列，仍为 `PENDING_ACCEPTANCE`。实施结果、证据与复算命令见[总报告中的第一阶段记录](../../docs/lidar/双束TLS候选实施与验证总报告.md)。

第二阶段新增 `diagnose_dual_beam_tls_residuals.py --package <TLS包> --baseline <旧法包> --output <新目录>`，对全部有效对进行评分后的方向/长度反事实分解、真实首交拓扑及表面偏移诊断，真值仅限诊断。`audit_dual_beam_observability.py --source <v3源包> --calibration <标定> --output <新目录>` 仅读取独立零载参考和固定标量，执行预声明解析 TEST 形状的首交/Jacobian/零空间探针，未验证形状先验时认证输出为 UNKNOWN。`verify_dual_beam_source_provenance.py --source <v3源包> --output <新目录>` 检查保存原始文件哈希、原始输出与侧车的一致性，缺失动态 VTP 单列证据缺口；可用 `--provenance-root <原路径结构的原始证据根目录>` 移机复核。三者均不启动求解器、不据此切换默认；见[总报告中的第二阶段记录](../../docs/lidar/双束TLS候选实施与验证总报告.md)。

第三阶段的 `run_dual_beam_validation.py --output <新目录> --reference <独立零载参考>` **启动独立单机 OpenFAST 求解**，复制完整输入并保留所有动态 VTP 的无损 XZ、原字节哈希、退出状态和共享数据预算；只能在物理求解任务内使用。`export_dual_beam_validation_source.py <run> <reference> <新源目录> --start <s> --end <s>` 核对完整原始时钟和零载参考，将真实 40/80 Hz VTP 导出为源几何，保持原 2 mm / 5 mm 接口容差。它不生成旧 B2 测量，也不启动求解器。`compare_dual_beam_time_refinement.py` 严格核对两运行仅 DT/DT_Out/VTK_fps 不同，分开保留共同保存时刻、全部细网格新增时刻、未知与经过最低值；两级敏感性不等于数值收敛或性能验收。已执行的 12 m/s / 9 rpm 工程工况、留存来源和复算入口见[总报告中的第三阶段记录](../../docs/lidar/双束TLS候选实施与验证总报告.md)。独立验证工况属于研究证据；0.3.13 安装包内置的是原开发片段的旧法和 TLS 结果，软件交付核对另见[0.3.13 发布核对](../../docs/blender/releases/0.3.13发布核对.md)。

当前布局为 S1/S2/S3 = 10°/12°/14°；S1 报警独立于双束有效性，旧 B2 回放仍可使用。40 Hz 保存记录不能替代设备原始采样或现场延迟验证。同一开发片段 400 对的旧法 / TLS MAE 分别为 4.268685 / 0.776096 m，两种方法仍为 REVIEW_ONLY / PENDING_ACCEPTANCE。[格式合同](../../wfrl/lidar/REPLAY_FORMAT.md#dual-beam-review-v1)和[发布范围](../../docs/blender/发布状态与验证范围.md)分别说明数据与交付状态。

原 0.3.12 ZIP 的记录检查为：T1/T2/T3 的 33/33/28 次完整过叶均有 S2/S3 同刻同叶片有效配对，共 94/94 次、400 个有效采样，每次至少连续 3 个保存采样点。这是保存网格的窗口统计，不能理解为每帧持续触发或禁止 S1 命中。S1 有效叶片命中继续独立报警，0.3.13 沿用该源数据及旧法结果，不因源码同步重跑 FAST.Farm。

## 历史 normal/close 生产流程

以下修订交付是 `results/lidar/packages/normal-v1.1` 与 `close-v1.1` 的本地 FAST.Farm 结果包。两者来自 36 秒真实求解的 18–36 秒窗口。`report.md` 给出逐束误差、手册定义、求交与真值参考点，以及解析和独立时空细化的全部实数。

使用 `/opt/anaconda3/envs/wfrl-mac/bin/python`；求解器位于同环境 `bin/FAST.Farm`，版本 3.5.3。后处理需要 NumPy；设置 `OPENBLAS_NUM_THREADS=1` 避免几何小矩阵调用启动大量线程。无需策略 checkpoint 或训练。规定转速 9 rpm、变桨 0°、无 ServoDyn；这是固定转速工况，不是运行策略的性能结论。

在仓库根目录执行以下命令。示例用新运行名保护已交付数据；正常工况用 8 m/s，较小净空工况用 12 m/s，其他配置一致。`--source-case` 可显式指定完整原始算例目录；默认来源见 `run_physics.py`，当前是保留的 NREL 5MW 历史算例。源目录不会修改。

```sh
export OPENBLAS_NUM_THREADS=1
/opt/anaconda3/envs/wfrl-mac/bin/python scripts/lidar/run_physics.py --name normal-new --wind 8 --fps 80
/opt/anaconda3/envs/wfrl-mac/bin/python scripts/lidar/run_physics.py --name normal-new-spatial --wind 8 --fps 80 --refine
/opt/anaconda3/envs/wfrl-mac/bin/python scripts/lidar/run_physics.py --name normal-new-temporal --wind 8 --fps 160 --dt 0.003125 --refine

/opt/anaconda3/envs/wfrl-mac/bin/python scripts/lidar/process_physics.py results/lidar/raw/normal-new
/opt/anaconda3/envs/wfrl-mac/bin/python scripts/lidar/process_physics.py results/lidar/raw/normal-new-spatial --evaluation-only
/opt/anaconda3/envs/wfrl-mac/bin/python scripts/lidar/process_physics.py results/lidar/raw/normal-new-temporal --evaluation-only

/opt/anaconda3/envs/wfrl-mac/bin/python scripts/lidar/validate_physics.py results/lidar/raw/normal-new results/lidar/raw/normal-new-spatial results/lidar/raw/normal-new-temporal evidence/lidar-implementation/validation-normal-new.json
/opt/anaconda3/envs/wfrl-mac/bin/python scripts/lidar/publish_physics.py normal-new normal evidence/lidar-implementation/validation-normal-new.json --destination results/lidar/packages/normal-new
```

较小净空重复该链路，把名称换为 `close-new`、风速换为 `12`、publish 第二参数换为 `close`。正常/较小工况依据先定的风速选取，不按照误差挑片段。每个工况先运行 19 个展向节点、原 DT，再运行 37 节点保持原 DT，最后保持 37 节点将积分时间步与输出时间步减半。这样分别检查空间差异与时间差异。完整 80/160 Hz 掠塔网格均包含漏测；没有只对旧网格有效命中配对。

`--evaluation-only` 只用于细化对照，仍计算固定掠塔区中每个预期样本的真值、三束首交与估计，并保留完整运动时钟。它不重复检查掠塔区域外的碰撞，明确输出 `collision_excluded: null`；不能用这种结果发布客户包。正式基础包处理必须不带此参数，它对每个保存时刻三片叶片执行保守分离检查。

保护与证据规则：

- 运行、处理、校验和发布均保留原始文件，不覆盖现有结果包；使用新目录名重算。publisher 在读取现有后处理数据与严格验证证据后生成新目录；不覆盖原始运行和旧结果包。
- 求解失败保存 `solver.log` 与 `exit_status.json`。当前首轮 160 Hz 处理失败日志仍保留：OpenFAST ASCII 时间固定四位小数，处理器验证打印误差不超过 0.000051 秒后，按已配置的物理输出采样索引恢复时间，不插造运动状态。
- VTP 原生表面来自 AeroDyn 节点位置、截面姿态和翼型坐标。原始完整表面大约每个基础工况 9 GB，37 节点/160 Hz 工况约 36 GB；原始输出存档与演示包分开，前端只读取小型 JSON 包。
- 坐标为 FAST 全局 x 下风、y 横风、z 向上。雷达 `[-2,0,87.6] m`；B1/B2/B3 相对垂直向下向负 x 偏转 `6.45/8.5/10.54°`。`Y_lidar=2 m` 对应手册图 2-5 主轴方向偏距；固定 `R_TIP=2.67 m`，不读取实时真值修正估计。
- 叶尖参考点是最外 AeroDyn 翼型截面周界坐标均值。塔为刚性圆锥，半径从塔底 3 m 线性降至 87.6 m 高处 1.935 m。该参考点的同高度塔壁距离不是整片叶片的全局最小碰撞距离。
- 正式包碰撞证书覆盖每个保存的 80 Hz 状态，不是连续时域无碰撞证明。细化数值是有限两级差异，不是严格误差上界；没有约定现场精度或算法验收容差。
- `validation.json` 的 full-grid sampling 条目保留新增采样点改变误差极值的证据；不得只引用小的匹配时间差异就声称所有误差收敛。

手册依据：MolasCL V3.0 第 3.5.3 节、图 3-26（印刷页 33 / PDF 页 34）；角度与塔筒半径定义见图 2-5（印刷页 8 / PDF 页 9）。原手册正文用 `X_lidar`、净空公式用 `Y_lidar`，这里明确映射为向叶轮方向的主轴水平安装偏距。雷达不模拟现场回波处理、硬件噪声或厂家专有融合。

官方原生表面依据：[OpenFAST visualization surface definitions, Table 4](https://openfast.readthedocs.io/en/dev/_downloads/5f2ddf006568adc9b88d8118dc3f1732/FAST8_README.pdf)。


## 验收修订的验证合同

新生成结果采用 `numerical-comparison-v2` 证据合同：包内保存数值比较证据，发布前检查解析结果、空间/时间运行链、物理配置、退出状态及完整采样网格，缺少内容不能标记 READY。`READY` 表示包完成且通过该合同检查；精度状态仍是未约定容差，不能解释为现场精度或数值收敛达标。旧版包保留供追溯和兼容读取。

发布窗口及采样率来自运行配置，时长必须大于18秒启动剔除段。`dist/lidar-delivery.json`、`dist/README-lidar.md` 保留旧 normal/close 的交付入口与摘要，不是当前 NREL 0.3.18 或 GW184 0.4.0 的完整清单。当前下载以[发布状态](../../docs/blender/发布状态与验证范围.md)为准。无需重跑 FAST.Farm 即可用完整既有数值证据重新封装这两个历史工况；修订不得改变原始逐样本测量。


原手册核验已补齐：用户提供的《MolasCL 激光净空监测雷达使用手册 V3.0》SHA256 为 `1a126ba6420f47136b17986064908215d6b0d7e0d574ff56247c1255b07a0363`。PDF第9页图2-5与第34页图3-26已逐图核对；两图X/Y命名存在坐标切换，本项目按向叶轮的主轴物理偏距映射。公式、相对光束角和名义塔半径含义一致，不代表真实设备精度验收。
