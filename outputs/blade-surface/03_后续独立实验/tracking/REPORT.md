# 二维自动跟踪与长窗口身份实验：实际结果

状态：`REVIEW_ONLY`。4次官方二维自动推理、短/长配对及RGB关键帧重启诊断均 `COMPLETED`；32帧材料交点身份延续与出框后重识别质量 `FAILED`。几何收益依赖 root 的数值门槛，本文不声称几何或现场测量收益。

## 已实际执行

- 本机 arm64、24 GiB RAM；本 run 内独立 Python 3.12 venv + torch 2.14.1，MPS 可用。共享环境未修改。
- 官方 CoTracker3 offline：代码固定 `82e02e8029753ad4ef13cf06be7f4fc5facdda4d`；单个官方权重 `scaled_offline.pth`，101,890,938 字节，SHA-256 `2670d4562ed69326dda775a26e54883925cd11b6fc9b24cb7aa9f8078bce7834`。HF revision `bf55ea50d4390e1820a267f131cd6587240fb2c5`。代码和权重多数 CC-BY-NC，完整许可文本与 SHA 在 provenance。
- 旧 attempt02 的 WideInput 132.0–132.7 s / 8 帧 / 10 Hz / 3840×2160 RGB，以及四个审核轨迹的首帧 query。未读取源状态、源 mesh、绑定、ID 或 WideEval。原文件只复制到本 tracking 的输入区，不改旧证据。
- 官方预测器、6 次迭代、6×6 全图支持点、无 backward tracking，工作尺寸 384×512。RGB 用 `bilinear / align_corners=True` 缩放，query 用端点比例缩放；官方预测器输入与其内部尺寸相同，返回工作像素；外部只做一次工作像素到原始 4K 像素的逆映射。
- 输出 `tracks[1,8,4,2]`；预测器 visibility 为 `torch.bool`，由模型 visibility sigmoid >0.9 得到。模型原始 visibility 和 confidence 单独保存；confidence 不是经过校准的身份/正确性概率。官方预测器会覆盖 query 帧坐标及可见性，因此主对应误差排除 4 个 query 帧。

28 个非 query 的审核参考样本中，模型判可见 28/28；原始像素误差 median **3.720 px**、P95 **7.631 px**、max **9.540 px**。一次参考差异超过开发诊断线 8 px。2 次最近审核角点属于另一条 corner 轨迹，但圆角候选接近且定位有歧义，不能称已确认的源材料身份交换。审核参考是 Codex 逐图 RGB 审核，不是用户人工真值，也不是源几何/源材料绑定；不能将此误差称为材料点或几何精度。逐点结果在 `old_short/result/metrics.json`，逐帧及 nearest-reference 诊断均保留全部样本。

原始像素160×160裁剪接触表 `old_short/result/crop_contact_sheet.png` 已查看全部32格：自动点（红）跟随补漆区域，末帧部分角点偏离审核点（黄）。不以该截图替代原始逐帧坐标。API 不变量检查通过：一次逆缩放误差 <0.001 px、query 坐标保持、全部32样本保存、bool visibility 与 float confidence 类型分离。

实际 MPS 推理 0.337 s，总运行 1.446 s；CPU peak RSS 818,495,488 bytes；结束时 MPS driver allocated 2,294,448,128 bytes。该 driver 值是结束时分配量，非峰值。未运行 CPU fallback、其他后端、训练或远程图像服务。

## 隔离与执行问题

`sandbox-exec` 实际执行：拒绝整个 wfcrl 工作区文件读写，仅许可本 tracking/runtime_tracking；网络拒绝。源 `source_material_bindings.json` 实际探针返回 EPERM，许可 annotations 返回 ALLOWED。Python open audit 完整保存在结果中。初次在被拒绝 cwd 启动 Python 导致路径初始化失败，改为许可 tracking cwd 后通过。初次 confidence 捕获用 PyTorch module hook，但官方直接调用 `.forward`，hook 未触发；推理真实运行后结果提取报 KeyError。失败记录 `old_short/failed_api_capture_01` 保留；改为本驱动只记录返回值的 wrapper 后通过，官方代码未改。

## 已冻结的长窗口设置

旧短窗只用于开发可运行性。`freeze.json` 固定单后端/权重/MPS/384×512/36 supports/6 iterations、32 帧（10 Hz，跨度 3.1 s）及同时间前 8 帧对照。保存48帧是捕获储备；主长配对在新自动输出之前固定为 **121.2–124.3 s**，短为 **121.2–121.9 s**。此起点与捕获开发8帧一致，不能按本自动输出重选。query/reference 是 RGB 四颜色边界交点，不使用色块质心或源真实顶点投影。材料身份延续、失踪、重现、边界漂移必须保留失败分母，不能用源叶片映射补身份。新 RGB 的审核对应和自动对应将分别存档；任何几何比较由 root 在数值门槛通过后实施。

## 新分散12点短窗实际结果

同12个 root 审核的 RGB 四色边界交点 query，8帧121.2–121.9s；已冻结审核 annotations SHA-256 `cac87b4260a9f1016b90ed195c04dbeb71f84a4734fc3d61c95dcec481f75ef1`。正式自动输出在 `new_short/result/`，原始像素逐帧、原始模型visibility/confidence和审核差异完整保存。

84非query样本全部预测可见，审核差异 median **4.248 px**、P95 **11.166 px**、max **17.343 px**；19/84差异超过开发8px诊断线。内/中段大多较小，M09–M11外段点更大。实际MPS推理1.221s，总2.483s，CPU peak RSS597,458,944 bytes。非query模型confidence min0.99724、median0.999765，但仍有17.343px差异，再次说明该值不能作为校准误差或材料身份正确概率。

`new_short/result/crop_contact_sheet.png` 包含全部96格的原120px crop，红cross自动、黄circle审核。长窗参考页在 `new_long32/rgb_reference_review/`，root已查看全部4页并批准。前8帧直接复用同已审核短窗坐标。384分母中，RGB可观测 M00–M07各32，M08=21、M09=20、M10=19、M11=18；其他null/false保留，绝不以源ID补缺失。该计数是RGB参考观测可得性；自动结果另见下表。

## 32帧主配对与身份失败

| 对照 | 完整分母 | 非query已观察参考 | 模型判可见/参考 | median px | P95 px | max px |
|---|---:|---:|---:|---:|---:|---:|
| 8帧短窗121.2–121.9 | 96 | 84 | 84/84 | 4.248 | 11.166 | 17.343 |
| 32帧长窗同前8帧样本 | 96 | 84 | 84/84 | 3.218 | 9.345 | 12.558 |
| 32帧长窗121.2–124.3全长 | 384 | 322 | 237/322 | 41.617 | 896.829 | 1158.740 |

主配对仅改变 offline 视频长度；相机、RGB、同首帧queries、后端/权重、384×512尺寸、36支持点、6迭代均相同。两次捕获生成的PNG文件SHA不同，但共用前8帧 **decoded RGB逐像素完全一致**（maxdiff=0，decoded SHA保存），query工作坐标bitwise相同；保留原PNG各自SHA，不伪称文件字节相同。

全384输出未删失；334个RGB参考可观察，其中12个query帧排除后322个可评分。50个RGB_NOT_OBSERVED样本仍在完整分母。85个参考已观察样本被模型判为不可见；50个参考未观察样本全部模型判不可见。后者不是源真实遮挡率。长窗322非query参考样本有228次差异超过8px开发诊断线；160次最近已审核RGB交点属于另一匿名码，其中模型判可见的237样本有80次nearest-other。该项是RGB对应诊断，不能宣称已核实源3D材料ID交换。

外段M08、M09、M10、M11在RGB中分别123.5、123.6、123.6、123.7s重现；重现后的9/8/8/7样本 **模型判可见均为0**，对应差异median约876.4/874.8/1134.4/1130.8px。原长轨迹没有成功重连同一冻结RGB交点。四个8帧时间块的median差异依次3.22、13.39、97.87、139.83px，全部逐点与缺失状态见 `evaluation/per_point_continuation.json` 和 `evaluation/paired_short_long.json`。已检查长窗末段原始crop结果 `evaluation/long_outcome_page_03.png`，模型坐标远离参考交点，且部分内段仍判可见；不能把bool可见直接当跟踪成功。

有限32帧覆盖明显转动方向变化、图像边界退出与重现。这里的缺失/重现通过RGB全图/边界记录，不读取源遮挡真值；**真实遮挡、遮挡后重识别及完整转动周期仍未验证**。同一回放片段不是统计独立现场样本。

## 独立RGB关键帧重启诊断

固定123.6–124.3s共8帧，全部其他后端设置保持相同；RGB匿名颜色码关联同一可见交点后重新query。M00–M10 query在123.6s；M11当时RGB_NOT_OBSERVED，首可见123.7s才query(t=1)。完整96分母保留M11首帧未知，官方模型在query前给出的坐标/false只是RAW_BEFORE_QUERY，不补算法身份。

| 同83个共享非query可比较样本 | 模型判可见/参考 | median px | P95 px | max px |
|---|---:|---:|---:|---:|
| 原121.2query长轨迹的对应尾段 | 38/83 | 139.828 | 1131.864 | 1152.327 |
| 新RGB关键帧query的8帧结果 | 83/83 | 5.246 | 11.324 | 21.036 |

两边均排除重启query的强制精确坐标，防止人为零误差抬高restart。结果支持RGB重查询后的短段局部跟随；**这是关键帧重启，不是自动全局重识别**。独立表在 `evaluation/keyframe_restart_comparison.json`，不混入主窗口长度排名。

## 资源、失败记录与审计交付

4次MPS推理依次为0.337、1.221、1.638、0.478s；总脚本时间依次1.446、2.483、4.459、1.539s，CPU peak RSS均低于0.83GB。长窗结束MPS driver allocated为5.390GB，这是结束时分配量而非峰值；资源不重复测量，不将缓存/编译影响下的单次时间称正式性能benchmark。完整资源及结果表在 `evaluation/resource_and_result_summary.csv/json`。

额外restart准备最初错误断言全部12点在123.6s可见，M11实际null/false触发AssertionError；长窗批准reference与固定case在断言前已保存，shell继续完成长推理。restart首次因case尚未建立而报FileNotFoundError，日志保留 `keyframe_restart8/inference_mps_failed_case_missing.log`。改为各点首个RGB可见时刻query后通过。`freeze.json` 的最终长输入身份补充是长推理之后、restart之前写入，**不称该补充哈希在长推理前存在**；此前长度/时段/backend已预声明，root审核reference及case确实先于推理保存，未改变主长数据或重选时段。说明见 `provenance/preparation_failure_01.txt`。

新实际存在的源场景副本、新材料绑定、留出WideEval三项探针均sandbox EPERM，许可annotation为ALLOWED，见 `provenance/sandbox_probe_new.json`。四次推理与独立评价Python open audit共4626请求；所有wfcrl读取只在tracking/runtime许可范围，禁止路径读取为0。复制编排只读明确许可的public RGB和匿名二维文件，原路径/hash见 `provenance/input_copy_manifest.json`。网络拒绝、系统沙箱读写限制贯穿模型与评价执行。官方代码无修改。

独立评价先核对保存推理结果SHA，再复算配对/重启/身份表。一次逆缩放、query保持、bool/confidence分离、完整输出shape、共用像素与query bitwise身份、冻结结果未改均通过，见 `evaluation/api_and_pair_invariants.json` 与 `evaluation/evaluation.log`。所有失败/不可见均保留，不以仅成功片段得分替代完整分母。

交付状态：二维后端实际运行与短窗比较 `COMPLETED`；32帧身份实验执行 `COMPLETED`、延续质量 `FAILED`；关键帧重启局部跟随诊断 `COMPLETED`；真实遮挡/完整周期 `NOT_VERIFIED`；自动几何收益及审核8vs长几何收益由root数值gate另判；现场健康、缺陷深度或载荷诊断 `NOT_ESTABLISHED`。

官方来源：[CoTracker repository](https://github.com/facebookresearch/co-tracker)，[固定 predictor API](https://github.com/facebookresearch/co-tracker/blob/82e02e8029753ad4ef13cf06be7f4fc5facdda4d/cotracker/predictor.py)，[官方固定权重](https://huggingface.co/facebook/cotracker3/resolve/bf55ea50d4390e1820a267f131cd6587240fb2c5/scaled_offline.pth)，[固定许可](https://github.com/facebookresearch/co-tracker/blob/82e02e8029753ad4ef13cf06be7f4fc5facdda4d/LICENSE.md)。
