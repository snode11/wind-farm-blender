# 叶片表面重建实际执行报告

本轮已完成所有可执行的冻结实验，最终 **COMPLETED_WITH_NEGATIVE_OR_GATED_RESEARCH_RESULTS / REVIEW_ONLY**。P2b三窗判定为 **FAILED_GATE**；长序列自动身份延续失败；当前凹坑条件未通过RGB可观测性门槛，恢复深度为null。没有现场几何精度或健康诊断验收。

授权及历史边界：本轮由来源用户明确授权实际执行，见 `plan_and_authorization.md`。此前proposal修订与P2b-0的DOCUMENT_UPDATE_ONLY/NUMERICAL_INCONCLUSIVE保留历史身份。所有新产物在独立run，原回放、原blend、产品模块和旧110个正式文件受到保护。

## 实际阶段与门槛

| 阶段 | 执行状态 | 结果 | 证据 |
|---|---|---|---|
| 共同数值前置门槛 | COMPLETED | NUMERICAL_GATE_PASSED_REVIEW_ONLY | [numerics/REPORT.md](numerics/REPORT.md) |
| P2b开发/60配对扰动/负对照 | COMPLETED | COMPLETED_REVIEW_ONLY | [p2b/development/REPORT.md](p2b/development/REPORT.md) |
| P2b三固定盲窗 × 四组 | COMPLETED_12_SOLVES_AND_12_EVALUATIONS | FAILED_GATE | [evaluation/blind_test_decision/decision.json](evaluation/blind_test_decision/decision.json) |
| 独立自动跟踪/重启 | COMPLETED_FOUR_INFERENCES | LONG_IDENTITY_CONTINUATION_FAILED | [tracking/REPORT.md](tracking/REPORT.md) |
| 自动轨迹同8帧几何对照 | COMPLETED | COMPLETED_DESCRIPTIVE_REVIEW_ONLY | [evaluation/automatic8_comparison/REPORT.md](evaluation/automatic8_comparison/REPORT.md) |
| 固定32帧联合几何 | COMPLETED_FINITE_FOUR_SOLVES_AND_EVALUATION | NUMERICAL_INCONCLUSIVE | [evaluation/long32_comparison/REPORT.md](evaluation/long32_comparison/REPORT.md) |
| 健康/颜色/凹坑局部对照 | COMPLETED_CONTROLLED_CAPTURES_AND_GATE | OBSERVATION_GATE_FAILED | [local_defect/REPORT.md](local_defect/REPORT.md) |
| 局部形状估计 | NOT_RUN_OBSERVATION_GATE_FAILED | FAILED_GATE; depth=null | [local_defect/decision.json](local_defect/decision.json) |
| 外观/实际Blender展示 | COMPLETED | COMPLETED_SOFTWARE_APPEARANCE_AND_DISPLAY | [appearance/REPORT.md](appearance/REPORT.md) |
| 旧正式证据/源码/dirty保护 | COMPLETED | PASS | [provenance/preservation_end.json](provenance/preservation_end.json) |

## P2b协议与开发敏感性

目标固定MODEL blade0（源T1/B1可见下侧与模板上侧对应，仅capture/evaluation知晓）。每窗8帧、4K、10Hz，求解仅WideInput；WideEval保留评价。四组为A1_new无点、C4中段M04–07、D4固定M00/05/06/11、D12全部12。各帧活动状态ψ/pitch0/flap0/edge0，其他9参数固定同一fresh A0；共同首帧公开RGB初始化q，不跨窗热启动。点项按可见数量归一到总权重12，sigma=0.391154px；归一、鲁棒项、先验、边界、FD、60预算和1e-7终止完全配对。

原TRF初始半径约1.43e-11导致停滞。唯一隔离修复是缩放空间初始半径下限1，未改变物理目标/边界/FD。8次真实有限数值对照、Jacobian/分支重放及实际禁读探针通过，随后开发dense/sparse目标网格差异0.128/0.491mm均小于1.667mm前置阈值。这是求解器数值资格，不是几何准确率。

公开K固定，T逐帧使用已批准值。Wide相机无parent/constraint，名义MODEL中心/aim每帧保持，World-up构造使MODEL相对roll变化约0.037872°；T的最大元素变化约0.059502主要来自−RC，不等于相机移动5.95cm。MODEL中心差仅约3.80μm。曾把T假设静态的准备检查在优化前失败，原证据保留；修为精确逐帧public合同，不改观测或数值设置。详见 capture/per_frame_calibration_explanation.md。

每个C4/D4/D12组实际做20次共享bias+逐帧像素扰动，共60次优化；无点A1目标不变，20个配对基线复用相同SHA，新增优化0次。δ_perturb=3.199mm，δ_method=0.020mm，δ_density=0.433mm；δ_dev=3.199mm、δ_min=6.398mm，像素保护带=0.250px。均为有限开发敏感性，不能当置信区间或现场噪声。

开发主组mean依次157.404/158.837/158.163/161.432mm；D12较无点更差4.028mm，虽部分投影指标更小，不能用投影改善代替表面恢复。空点A3与A1字节相同；半数换点、错误叶片和错误侧负对照保留原结果。错误侧用尽60预算且大量query失败，未建立自动拒绝错误身份的可靠性。

## 三个固定测试窗口

测试时间143.0–143.7、148.5–149.2、165.0–165.7s；每窗四组12重建及共同12 query全部冻结后才读取测试表面真值。窗口由有限RGB谓词第一eligible选出，未按源phase或几何分数挑选。初始可见标记方向均约−25°至−30°，phase覆盖有限；三个保存回放片段不等于独立现场样本。

主表面量使用同MODEL/时刻/正确侧，按不可变材料xi剪切内[.18,.35]/中[.45,.65]/外[.75,.92]，global为三带并集。双向距离各自按当前三角面面积均匀采样512点，方向各0.5、帧/clip均权；P95为混合分布分位数，无ICP或尺度/姿态对齐。每组q_eval独立于q_fit，使用同一批首帧公开RGB射线，在冻结估计网格上生成；评价进程不初始化q或更新状态。

| clip | group | global mean mm | mixed P95 mm |
|---|---|---:|---:|
| test01 | A1_new | 160.357 | 362.725 |
| test02 | A1_new | 153.824 | 385.712 |
| test03 | A1_new | 189.882 | 556.916 |
| test01 | A3_C4 | 161.726 | 366.120 |
| test02 | A3_C4 | 153.494 | 384.060 |
| test03 | A3_C4 | 190.876 | 562.836 |
| test01 | A3_D4 | 161.430 | 364.343 |
| test02 | A3_D4 | 150.262 | 385.984 |
| test03 | A3_D4 | 193.321 | 560.219 |
| test01 | A3_D12 | 161.728 | 365.690 |
| test02 | A3_D12 | 153.975 | 383.492 |
| test03 | A3_D12 | 192.847 | 558.963 |

最终判定 **FAILED_GATE**。正式有效主值（mm）：A1=null，D12=null，gain=null。缺失/失败时这些值为null，原始有限诊断另表，不能按成功样本排序。完整内/中/外/global表、36首帧与252后续WideEval点分母、逐clip门槛和投影保护见 [判定](evaluation/blind_test_decision/decision.json) 与 [区域CSV](evaluation/blind_test_decision/region_rows.csv)。

三个RGB定位门槛的全8帧P95依次为1.586/1.519/1.315px，均超过冻结的1px阈值；窗口02的D4用尽60次预算，未成功终止，是完整性审计唯一失败项。原始诊断中D12三个窗口的表面mean均更大，三窗平均差为1.496mm；这些有限诊断不恢复已被门槛置为null的主结论。

## 自动轨迹与固定长窗

官方CoTracker3离线权重及commit冻结在隔离runtime；主短/长窗同首帧query，restart按各点首个RGB可见时刻query（M11在restart第2帧）。384×512工作尺寸只缩放一次，输出只回4K一次。新8帧后84条相对Codex逐图审核RGB参考的median/P95为4.248/11.166px；相同前8帧的长窗结果3.218/9.345px。固定32帧共384计划观测，334有RGB参考、50条RGB_NOT_OBSERVED保持null；全部322条可观察非query参考中237预测可见，全部322误差median/P95为41.617/896.829px，未按237可见子集筛选。M08–11 RGB出框后重新出现，自动可见均0，长身份延续FAILED；真实遮挡、遮挡后重识别和完整周期未验证。RGB关键帧重新query保留96计划槽、95可观察、12query、83非query；同83尾段原长轨迹139.828/1131.864px，重启5.246/11.324px。这是显式重新起轨，不是自动全局重识别。参考并非用户人工或source真值。

同8帧自动轨迹几何三组mean为无点157.404mm、审核161.432mm、自动174.277mm，未建立自动轨迹收益。自动评分query仍是相同审核RGB首像素；不用源真值重定位。

固定32帧实际做A1/D12×dense/sparse四次联合求解，128活动状态、D12另24q变量，50null不补。数值资格为 **NUMERICAL_INCONCLUSIVE**：D12的dense/sparse目标叶片网格平均差为2.322mm，超过1.667mm阈值。原8帧decoded RGB/mask/标定/query和fresh A0完全配对；共同前8帧与全32不同时间的指标分别记录，见 [长窗几何报告](evaluation/long32_comparison/REPORT.md)。未通过数值资格时增益为null，诊断不转成结论。长窗没有独立WideEval或自动后端三盲窗扩展。

## 局部缺陷与外观展示

健康/仅颜色/真实50mm内凹×3时刻×2视角共18正式4K RGB。第一轮18图已渲染但float32元数据序列化失败，保留；仅修序列化后另label再做18，实际36图。全部6个健康/凹坑decoded RGB逐像素完全相同，颜色对照有2715–3966变化像素。局部7.86m²面积双向几何分离约mean10.656/P9537.409mm是两幅图像等价表面的差异，不能叫恢复误差。

因此OBSERVATION_GATE_FAILED，有限局部形状估计NOT_RUN，真实恢复深度和颜色假阳性深度都是null，不能写成0。该结论只针对当前Workbench FLAT MATERIAL、内凹和固定相机；不推论所有照明/相机下均不可恢复。

A2_new纹理保持A1重建字节完全相同；A3纹理也在冻结网格上采样。未知/冲突显式保留，atlas texel数不能解释为物理表面观测覆盖。两包各77项独立factory/source-snapshot Blender软件检查（8state真实timer_tick提交、网格、UV、packed/reopen及损坏sidecar）共154项通过，但需要独立内存unknown adapter：原reader读取缺observed_sections字段的新recon失败，证据保留；adapter仅内存补全0UNKNOWN，保存重开仍需它。未声称直接产品兼容或工程播放验收。静态完整三叶片UI v2由root独立查看，冻结recon字节及6网格局部顶点/UV未改，见 [Blender比较](validation/display/comparison/comparison_full_frame_v2.blend)。

## 隔离、资源与保留边界

capture与独立evaluation可读源真值；solver、initializer、tracker、texture只读指定公开输入/冻结模板/已声明runtime。macOS kernel sandbox和Python repository whitelist同时实施；对实际存在的source geometry/binding/projection/WideEval/blend做EPERM且0bytes探针。正式完整性状态为 **FAILED**；逐项q_eval冻结、状态/文件哈希、公平输入合同、完整分母及solver终止资格见 [完整审计](evaluation/formal_integrity.json)，不能以多数检查成功抹去任何失败。失败启动/旧partial截图/错误侧或正式组预算耗尽/序列化故障都保留，未把审计失败伪称全记录。

正式P2b产品64张RGB，另scout41/long48/duplicate Input8，实验原图渲染161；局部对照36张，共197张实际实验capture RGB，复制文件不增加渲染计数，display render/UI截图另列。60配对求解累计优化墙时3888.99s是并行任务求和，不是总elapsed。所有solver子进程全局最多3、BLAS线程1；四个成功自动case的MPS推理CPU进程峰值RSS<0.83GB，长窗MPS driver结束分配5.390GB是末值，GPU峰值未测。各任务耗时/RSS/调用数、原图字节账与artifact清单见 [资源总表](resource_ledger.json)。

保护审计：25源身份、110旧正式文件、18旧provenance、HEAD及原staged/unstaged patch（除允许的proposal README追加）均通过；README原始字节prefix保留。历史94个recoverable删除未恢复，旧1日志差异不归本轮；未将544个旧清理项当正式110分母。原dirty状态保存，未reset。未重跑FAST.Farm/MAPPO或训练，未提交/推送/发布/上传RGB，也未安装到日用Blender扩展；安装版无写入操作，未声称做了全安装树起止hash。

完整复现及身份验证见 [REPRODUCE.md](REPRODUCE.md)，各阶段日志、读审计、逐点/逐帧数组及图像都保留；不以计划、进程退出0或截图替代准确率证据。
