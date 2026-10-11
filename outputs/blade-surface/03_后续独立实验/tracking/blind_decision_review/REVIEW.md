# 三窗盲测准入复核与限定修复

状态：COMPLETED_SYNTHETIC_GUARD_CHECKS / REVIEW_ONLY。先只读复核旧脚本，发现完整性与失败状态缺口；root随后明确授权本agent修改唯一文件 `evaluation/scripts/decide_blind_test.py`，暂停其本人对该文件编辑。已完成修复及30个纯自造fixture检查。未读取任何实际surface/material评分、source geometry、private或WideEval图像；没有重复推理/纹理提取，没有修改其它root评价脚本。

旧脚本SHA `d36472ceff5fd52b18819ec2dcbfa2e2d35e951dcdf5032109d575dfcfbc50cb` 的原字节保存在subject_snapshot_v1.py。新版SHA `c2201a0d4eec1d7a2ca819e712aa29322e34d26fb3e94528a6cb2f64174b092b` 保存在subject_snapshot_v2.py。读取的protocol是experiment_protocol_staged.json，SHA `1c3ad6ce3adf456a2b528d4394ef094698601d0a0fe52c040f44cf216550c668`，状态仍STAGED_NOT_FORMALLY_FROZEN，三个开发阈值为null。fixture的非null阈值/FROZEN字符串只是自造测试输入，不是本run正式冻结协议。root声明修复发生正式capture/scoring之前；本复核不靠mtime推断先后。

## 公式确认

原均权mean保持不变：每窗8时刻、每时刻2方向、每方向512样本，逐窗mean再等权平均三窗。P95混合全部 `3×8×2×512=24576` 个距离值，不平均三个窗的P95。inner/middle/outer/global分别聚合，global采用评价脚本已经采样的global数组，不以三区均值替代global。

主比较仍为A1_new对A3_D12。三窗mean需同时满足10%相对增益和δ_min绝对增益；至少2/3窗各自同时满足两项；逐窗退化不得超过 `max(5%×baseline,δ_min)`。各窗baseline均需严格大于δ_dev，否则INCONCLUSIVE_NEAR_BASELINE。这是全部三窗条件，正式protocol应写清。D4与C4的同4点比较仍是分布/对比度/尺度/可定位性等共同差异的对照，不是纯空间分布定理。

WideEval首帧/后7帧分母固定36/252（3×12、3×7×12）；保护项为两split各自median/P95。主比较两组完整且D12减baseline不超过保护带才通过。无alignment或registration。

## 修复的缺口

旧版共同freeze结论硬编码true，仅查帧数/密度及部分NPZ长度；summary mean与NPZ P95没有一致性检查；投影只查行数而不查完整身份集合；异常没有FAILED/null文件；主比较投影缺失仍分类NO_CONTROLLED或INCONCLUSIVE。新版均显式阻断：

- mandatory --common-freeze 要求exact24个canonical绝对路径，恰为12个summary.recon加12个q_eval_consumption.path，路径唯一。当前SHA/bytes对manifest；recon SHA再对summary、q SHA再对consumption。consumption须说明不在评价进程生成，并保留12queries。
- 所有12个summary须EVALUATED_REVIEW_ONLY、8帧、density512、exact protocol test_frame_ids与两camera及no_registration=true。
- NPZ须exact64 keys，命名沿用 `{frame}_{region}_{direction}_m`；每数组shape(512,)，有限且非负。完整方向样本的mean/mixed P95对summary交叉一致（rtol1e-10、atol1e-12）。
- 每material表exact192唯一组合（8×2camera×12M00–M11）；frame/index/split/hash核对，不以重复身份补分母。
- A1/D12任何投影失败优先FAILED_GATE/FAILED，aggregate_surface与baseline/D12/gain/relative主值null。失败split的主median/P95为null，有效子集仅valid_only_diagnostic；可计算几何仅留diagnostic。一般读取/schema/数值异常也写decision.json/failure.json，保留完整计划分母，CLI退出1。

投影完整但保护带超限仍为NO_CONTROLLED_GEOMETRIC_GAIN，这是有效准入未通过。按root指定，C4/D4投影缺失只使其split主统计null，不阻断A1/D12主几何判断。

all24_common_recon_q_hashes_verified来自实际24项核验。旧all12时序字段还须integrity.status=PASS及显式common_freeze_before_any_test_scoring=true。manifest自身无法证明评分先后；时序来自run_surface_batch先写freeze再dispatch的执行链与root integrity，不凭mtime。

## 实际自造检查

旧12反例保留于fixtures/、synthetic_results_v1.json；新版30案例保留于fixtures_v2/、synthetic_results_v2.json，全部命中预期。覆盖均权/P95、2窗及容许/超限退化、仅1窗增益、投影保护带、投影缺失对near-baseline优先级、summary/NPZ不一致、FAILED summary、shape/length/key、NaN/负距离、重复身份、缺失文件、freeze23、q当前hash改变、summary SHA、帧/camera、时序/输入/integrity、staged/null/改变门槛。

自造baseline三窗mean为0.1/0.2/0.6 m时，聚合mean=0.3而混合P95=0.6，确认未平均P95。另例D12首帧仅一条预测出框，保留计划36/有效35/失败1，主median/P95为null，状态FAILED_GATE；可算0.02 m增益仅diagnostic。这些数值全部来自自造常量，不是本run几何结果。

自造运行通过OS sandbox与Python白名单，新版3639个read events、禁止读取0。OS默认拒读整个wfcrl，仅允许review目录和只读trial runtime，禁network。另真实CLI合法fixture退出0，缺mandatory --common-freeze退出2。独立explorer只读复核新版，未见新的实质公式/计划分母缺陷。软件检查不能替代正式协议冻结、实际12组评价与root完整性证据。
