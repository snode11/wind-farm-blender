# 分散颜色点与隔离捕获报告 · 2026-10-06

捕获侧已完成开发、选窗、48帧长序列、独立留出补捕及固定三测试窗48张双路RGB。root审核三张96格junction sheet后批准冻结全部288个公开RGB交点，随后批准最终mask；annotations不是用户人工或learned自动后端。root独立观测评价报告三窗均未通过冻结的1原图px gate：本轮正式输入保留为gated诊断，不改annotations/mask或重选，后续固定12组求解/评价由root管控，正增益判定应保留FAILED/null。本agent未读取根评分、具体源点误差或源坐标反馈，不据捕获图声称几何收益。

实际源为 `outputs/blade-recon-mappo-two-camera-20261004-a02/MAPPO_original.blend`，SHA-256 `1f1ff1d5211c54bd513eb272e4c1d3d99dffe6df5d1a4e511d5cff4566cf1328`。复用保存117–177s/40Hz回放、工作区 Blender 源码和源场景记录的安装资产，只写本 run。没有重跑模拟/训练、覆盖原 blend 或日常安装、推送/发布或上传图像。颜色只追加于 source T1/B1 的既有 polygons；材料赋值前后同刻动态顶点字节和拓扑相同。静态 rest chart、动态 mesh、绑定、源身份、WideEval 和保存副本均在 `../private_evaluation/`。

相机沿用 WideInput MODEL中心(100,0,90)、WideEval(100,-20,90)，瞄准(5,0,90)，75°水平FoV、near10/far1000，3840×2160。每帧先在只读handler事务内寻址并显式 `FarmFlex.update(sim_time_s)`，确认已提交时刻，然后按相同MODEL光心/瞄准设计值更新世界相机并渲染。Experiment相机无parent，采用World-up朝向规则；完整MODEL→CV外参逐帧已知且动态，消费者须使用public calibration各帧K/T，不能复用首帧摘要。公开开发窗K相同，T最大元素差0.0595015，反算MODEL光心仅有约3.8微米舍入差；原因与转换见 [逐帧标定说明](per_frame_calibration_explanation.md)。Workbench FLAT MATERIAL / Standard / RGB / 8xAA；保留真实结构遮挡，关闭教学ghost。它们是受控材料颜色图，不是自然光照/现场视频。

内、中、外带各4个四颜色边界交点，总12点，含弦向与展向分离。查询定义是既有材料vertex处的四面交点；实际vertex/face绑定只在私有评价。公开匿名M00–M11由观察到的红色RGB编码和三灰象限区分，未用圆斑质心、真实q或源ID补观测。开发121.2–121.9s/8帧，root逐格审核96个crop后冻结 `CODEX_RGB_REVIEWED`，不是用户人工标注或学习后端自动轨迹。root独立评价汇报身份漂移全帧P95≈0.931px、后7帧P95≈0.977px；该独立评价不是正式定位输入。

有限3次输入提取设置给出共享偏移component RMS0.14730px、帧级去均值component RMS0.21902px；P95向量幅度分别0.47018/0.69214px，总差P951.13491px，max2.23607px。原始数组保留，来自RGB提取设置敏感性，不是物理定位精度、人工标注方差或统计CI。

初始neutral/red掩码对蓝/绿负例实测色核纳入率为0，新公开RGB规则为1，修复620色核孔洞。正式红系图仍有抗锯齿转换产生的矩形薄孔；第一版RGB凸包未完全修复。最终一次线性sRGB palette修正仅限RGB识别红色块bbox+4px与既有闭合RGB ROI相交，容差6 raw RGB，不作全局填洞。000046的12个色块bbox内部残余孔洞均0，内部总孔洞807→176（scope外剩余保留），外ROI像素变更0。root批准最终开发RGB/mask；`mask_manifest.json` 明分historical pre-repair和canonical final SHA。ROI是RGB最大neutral/red连接组件外轮廓，包含图像连接的实体hub/tower，不能称source-ID blade-only mask。

选窗只用输入RGB，未用源azimuth/state/binding/ID/留出或几何分数。候选固定时间表v1共24首帧；第二stratum无合格，原失败保留。root允许唯一v2半格时间列表8首帧，仍用同一RGB谓词/首合格规则，无无限追加。最终test start143.0/148.5/165.0，每个start+[0,.1,…,.7]；各窗不重叠，排除旧131.8–137.0及本次120–124.7开发。三个start的RGB方向约-25到-30°，相位覆盖相似；不能称广泛旋转相位泛化或统计独立现场样本。正式8帧若出现观测失败，保留分母，不按得分重选。

长捕获固定120–124.7s/48帧。tracking在新自动输出前冻结primary32为121.2–124.3、短同起点121.2–121.9；只读许可RGB/匿名二维观测/单路标定。RGB reference候选全384分母保留，出现部分外点出框和重现，false/null不由源ID补齐；root已审核全部可观察接点，固定32帧公开bundle现为334可见/50个RGB_NOT_OBSERVED；前8帧RGB像素、mask字节和交点坐标与短窗逐值一致。

实际开发双路补捕16张：8幅重复WideInput的PNG容器SHA不同，但8幅解码RGB像素全部exact equal，changed pixels=0；冻结原输入未替换。8幅新WideEval只在private。补捕耗时7.07s（采集循环），peak RSS1.446GB/macOS arm64，投影约定检查max0.000313px(Input)/0.000572px(Eval)。这是像素/数值约定检查，不是计量精度。

当前161次实际RGB渲染：正式开发产品16 + 正式test48 + 输入可见性scout41 + 长序列48 + 重复开发WideInput8。正式双路产品合计64张，额外数量不混称主实验。逐原图SHA/尺寸/字节见 `raw_rgb_ledger_final_capture.json`；正式前113+48 PLANNED历史账保留于 `pre_formal_readiness/`，复制的long32 public bundle不再计render。三正式窗采集循环8.386/8.369/8.157s（共24.911s，未含Blender启动与RGB后处理），macOS peak RSS约1.404/1.405/1.509GB；早期capture没有RSS字段，保持null，不追造资源测量。

正式执行使用原冻结 `run_formal_capture_wrapper.py`（SHA `f26f3fb5d9dbfc2efa9dcc73885995075e496ebe44bfa6e7f407416fd90bb926`），global `experiment_protocol.json` SHA `b295e6437e9abfd89ffcc938eb1931ebedd6c18857f1292c45283358f7e84926`；wrapper启动前校验7capture算法、原blend及完整6source模块SHA，未改变窗口/算法。三窗原图frame260–267/315–322/480–487，全部4K RGB；WideInput各8幅在public、WideEval各8幅及动态mesh/绑定/投影/source copy在private。root review evidence与candidate SHA写入各 `rgb_junction_review/review_freeze.json`，manual annotations采用开发同一v1 schema、保留全部96 planned slots、缺失false/null规则；此次288/288图像可见，图像可见不等于材料身份gate通过。

各窗最终全部21个公开文件的canonical SHA见 `formal_testXX/public_hashes_after_annotations.json`；`public_hashes_final_before_annotations.json`、`public_hashes_final_after_annotations.json`为对应历史阶段记录。最终交付入口 `formal_public_delivery.json`，只含许可RGB/标定/同mask/匿名审阅二维数据。mask内部历史pre-repair SHA与final_frames分离；root批准后只更新review状态元数据，mask像素再无变化。三窗完整mask contact sheet、96格marker AA overlay、逐帧前后thin-hole审计均保留。当前5种private文件×3窗实际存在的纯路径清单为 `formal_private_probe_paths_actual.txt/json`，供独立系统sandbox拒绝探针，不将内容交给solver/tracking。

保留失败：第一次保护检查错误地比较rest与动态mesh，尚未渲染即退出；随后改为同刻着色前/后检查。v1第二stratum选窗失败；palette修复v1剩余薄孔；早期private commit的通用blade hash实取循环最后B3而未明指目标，正式driver已明确T1/B1 target hash与三片hash，早期source_geometry NPZ本身正确，原字段不覆盖。

读取审计分层：RGB提取/标注/重复性/mask只读公开RGB和其候选记录，SHA见相应JSON；捕获允许读取源真值。正式driver新增Python open audit及显式native blend/manifest replay输入身份，审计不冒充整个Blender启动及native内部的完整OS syscall trace。solver/tracking的实际系统沙箱由root另管。


## root独立观测门槛最终记录

正式48张RGB与三窗公开输入均已冻结，root实际查看288个junction格与24帧完整mask；候选/annotation先冻结后才读源投影。定位1px gate三窗均FAILED。以下P95为所有96槽和后84槽，非成功样本筛选：

| 窗口 | 全96 P95 px | 后84 P95 px | gate |
|---|---:|---:|---|
| test01 | 1.585535 | 1.524423 | FAILED |
| test02 | 1.518906 | 1.459575 | FAILED |
| test03 | 1.315121 | 1.419666 | FAILED |

没有调整公开坐标/mask、重选窗口或把1px门槛放宽。后续固定12组为原设置诊断，主几何结论必须FAILED/null。完整root评价在 ../evaluation/test_observation_gates.json；capture没有消费源UV来修标注。原RAW实际161（正式64）、copies不算render。
