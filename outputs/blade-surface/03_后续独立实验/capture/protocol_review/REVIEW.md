# §16 冻结合同只读复核

状态：`READ_ONLY_PROTOCOL_REVIEW / FORMAL_TEST_NOT_STARTED`。复核范围是 root 指定的四个协议/recipe/helper 文件、v3 §16、capture 配置与 wrapper 身份；没有读取新 surface 评分、数值拟合结果或 source 几何反馈，没有修改 root/production 代码。各原文件的本次 SHA 与 source 身份核验保存在 `review_identity.json`。本报告不构成正式执行授权。

`experiment_protocol_staged.json` 明确为 `STAGED_NOT_FORMALLY_FROZEN`。δ_dev、δ_min 与原像素投影保护带仍为 null 是 root 正在完成的已知开发依赖，不应误记为已冻结或实验失败。正式启动仍等待 root 一次最终冻结。

## 已覆盖的合同

| 合同 | 本次只读证据 | 结论 |
|---|---|---|
| 三个非重叠测试窗、8 帧/10 Hz | staged lines 38–69；capture 配置列出 143.0–143.7、148.5–149.2、165.0–165.7 | 三窗具体时序一致；两相机共 48 张正式测试 RGB，配开发双相机 16 张合计 64。48 张测试尚未捕获。 |
| 目标/分组/公平初值 | staged lines 9–37、70–75；recipe lines 5–7；helper lines 17–35 | blade0、A1/C4/D4/D12 子集、fresh A0、共同首帧 q0、相同 config/scale/budget 与其他 9 参数固定均有声明。helper 按值继承开发配置。 |
| q-independent 主指标 | metric lines 4–36 | 同 MODEL/米制/时刻，固定侧面、参考材料 xi 分带；双向点到三角面，各自面积采样、方向等权、窗内 8 帧等权、测试 3 窗等权，P95 合并分布而非均值 P95；无额外对齐。 |
| 共同 q_eval | metric lines 38–50；recipe line 7 | 所有方法共同 12 首帧输入 RGB 像素，在各自冻结估计 blade0 上只读定位；不更新几何，fit q 分离，首帧/后 7 帧分列。 |
| 源隔离与留出 | staged lines 71–75；recipe lines 3、9；helper lines 13、25、31、35 | 唯一 WideInput；WideEval/源 mesh/绑定/状态/ID 不进求解/初始化/纹理；每步独立 sandbox 和读取审计有明确流程。此文本复核不替代 root 的实际拒绝探针及系统审计。 |
| 失败分母及修复 | staged lines 85–89；metric lines 43、49；capture 配置 failure 字段；wrapper line 18 | invalid/miss/缺帧为 FAILED/null 并保留 planned 分母；失败不替换，估计修复须降级旧 test 并选未用数据；同回放与相似相位限制已写明。 |
| 主收益规则 | staged lines 77–84；metric line 51 | 10%、至少 2/3、任一窗退化带与开发经验敏感性公式已声明；δ 的待定状态保留。 |

## 正式启动前应消除的两处歧义

1. **测试评价必须跨三窗一起等待。** staged line 86 要求全部 12 个测试几何及共同 q_eval 冻结后才开任何 test surface scoring，与 §16.3.5 一致；recipe line 7 的“整窗 groups_hash_frozen.json 后由 root 独立评价”容易被执行者理解为完成一窗便开评价。建议 root 将 recipe 明确为：每窗可产生并冻结几何/q_eval；三窗 12 组全部冻结、完成总清单后，才统一开启任何测试评价。当前仅发现文字歧义，没有证据表明已发生提前评价。

2. **helper 的输入核验粒度弱于冻结清单。** helper line 18 检查 8/32 帧计数与 A0 state 形状，line 22 检查每帧只有一个 camera，但没有在这个文件内检查 camera 名称恰为 WideInput、具体 frame/time 列表恰为本窗预声明时序、A0 的 frame/time 与输入逐值匹配、原图分辨率/标定身份符合该窗许可清单。建议在 root 的 canonical input 交付/启动校验中显式验证这些值与 SHA，并保存证据；或由 helper 所属 agent 在正式冻结前添加 fail-closed 核验。这里未读取 loader/guard/solve driver，不能断言其他层完全没有这些检查，也不将本文件的声明视为已证明运行时隔离。

## 最终机器可读 freeze 应链接的字段

本次四文件以 references 复用开发配置，避免重复参数是合理的。最终 `experiment_protocol.json`/总 manifest 应包含被引用文件的精确 SHA，使这些 references 不能在测试间漂移：输入/annotations/最终 mask `final_frames`、开发 config/preregistration（sigma/q 域/块归一化/尺度/预算）、capture 配置与选窗 v1/v2/失败记录、评价协议及实际代码、blind decision 代码、20 次配对扰动 shared/frame 幅度来源与最终 δ。

§16 的分布假设（D4 vs C4 至少 2/3 同方向且均值差超开发稳定性带）、近零基线 `INCONCLUSIVE_NEAR_BASELINE`、WideEval median/P95 保护、拟合 q 与共同 q_eval 分表、同区域 RGB 占据/边界次指标，没有在这四文件中全部展开；staged 指向 blind decision 代码 SHA，但本任务没有读取该代码或相关评分输出。正式 freeze 需通过独立条款或精确引用覆盖，不应仅凭本报告称这些执行细节已验收。

## Formal wrapper 与原码门槛

`run_formal_capture_wrapper.py` SHA256：

`f26f3fb5d9dbfc2efa9dcc73885995075e496ebe44bfa6e7f407416fd90bb926`

本次核对 7 个 capture 算法当前文件及 frozen byte copy 均匹配 capture 配置 SHA；原 `MAPPO_original.blend` 匹配 `1f1ff1d5211c54bd513eb272e4c1d3d99dffe6df5d1a4e511d5cff4566cf1328`。wrapper lines 12–14 使用 private `development_heldout_pair/source_identity.json`，在正式捕获前校验全部 **6** 个依赖 module 的实际文件 SHA，而不是仅校验 addon `__init__`：

| module | SHA256 |
|---|---|
| `__init__` | `7750f546ea44232ebbc5d6a308be54e540c0a0883a3add5fb9a44f07b7b50e02` |
| `farm_flex` | `3bc192697eddcd5707cb575d30296e32d28a4cb6b694388c8dd1fe2a89275d2a` |
| `custom_camera_capture` | `bd4aa555329cc528a3a28cc53ccd48d492cace52c7343830cbf07efb073d1bfa` |
| `turbine_geometry` | `e6d3779f110134a2132c50862f3e37cb388407c5bd28d2291458edfc7b0251fa` |
| `repair_marks` | `bf31e29951c64ae884d15d2a92319938dfb96ed2f64e2cd59a25b9246c8388eb` |
| `materials` | `f7e16899c9728b1d13b2198d866f016793c61ea1fd665509d56a3eda8b781564` |

六项当前文件均匹配。wrapper 本身只要求 `--global-freeze` 指向存在的文件并记录其 SHA；该文件必须由 root 确认是真正最终冻结产物，不能传 staged 文件。启动授权和最终冻结状态由 root 把关。source identity 只用于 capture/evaluation 的原码门槛，不交给 solver/tracker。

核验日志：`review_identity.json` SHA256 `ff197279b616920d8534d8cfc33cbd42f41cf238adb3b33fd9eb664aae266bdb`。本次检查时没有 formal_test 目录。未重跑捕获/求解，未改变冻结算法、全局配置或阈值。
