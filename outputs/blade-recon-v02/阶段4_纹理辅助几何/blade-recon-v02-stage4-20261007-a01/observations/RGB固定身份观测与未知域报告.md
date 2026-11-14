# RGB 固定身份观测与未知域报告

2026-10-07。此子任务已实现、运行和冻结原 RGB 固定锚点跟踪。**最终是部分质量门控观测，不能据此宣布实际 Patch/Crack 独立对应、完整实例或第4项科学验收通过。** 正式几何候选、轮廓消融与独立留出评分由主流程另行冻结和判定。

本次使用仓库 AGENTS.md 与 `.agents/skills/windfarm-blender-dev/SKILL.md`。保留原 RGB、原 mask、已声明未知域、所有前轮负结果及他人修改；没有重跑 FAST.Farm，没有操作任何已有 Blender 窗口。

## 已落实的代码与合同

`blade_recon0.2/surface_tracks.py` 从首次 fit 原 RGB 的固定传感器 ROI（x=.25–.75W，y=.20–.80H）提取 Shi-Tomasi 角点并亚像素化。没有读取已有缺陷边界评分文件，没有将修补坐标或裂纹坐标复制成观测。后续每个时刻直接对首次 RGB 锚点匹配，原锚点 ID 永不替换；第一次失效后保留该观测原因，后续全部 `IDENTITY_TERMINATED`，不再获取。

原 ROI RGB 经固定40px低频背景扣除后，使用原图仿射 ECC 多启动估计图像运动。多启动角度固定为 -30/-15/0/15/30 度；只按 RGB 相关系数择优。去掉这一运动后，在仿射归一化原图上作局部 LK 与独立逆向 LK，避免旋转形变及人工 warp 边缘在粗金字塔上导致跳失。描述子使用固定锚点 RGB 经同一 RGB 仿射变换后的25px窗口；整个固定 ROI 中的独立竞争峰距主峰必须超过8px。不能把 ECC 全图配准或小的局部条件方差当作三维精度。

质量门保持：条件定位 sigma≤1px、原锚图前后向误差≤1px、NCC≥.8、RGB对比≥12灰度、独立竞争峰 ZSSD 比值≥2。NCC 以数学范围 [-1,1] 截断，修复浮点舍入造成的1.00000036，并没有改变 .8 门槛。sigma 是仿射亮度拟合残差与局部 RGB 梯度 Hessian 得出的条件平移协方差；它不是独立真值误差。几何模块另使用最小 .25px 的权重下限，不能以此条件 sigma 宣称 .1px 物理定位已验收。

新增 `rgb_masks.known_observation_domain` 仅把已声明原像素未知多边形转换为独立 known 域，完全不修改测得 occupancy。候选点的整个描述子必须落在 known 且有测得叶片支持的域中。宽景塔/机舱多边形仍未知，没有补叶根、补未知洞或从源几何补完整轮廓。

输出使用 `blade-texture-geometry.v1`；每条 track 的 `anchor` 保存首次 frame/t/camera/uv/RGB SHA，每个计划时刻保存原像素 uv 或 null、accept/status/quality。几何侧只能在自己的首次轮廓估计健康网格上射线绑定一次；本模块不读任何网格，不能重绑到源表面。Patch 和 Crack 相机的角点分别有独立 ID；没有以相机名或区域名将跨相机点身份拼接。

## fit 实际运行结果与保留的分母

使用既有新128采样 MAPPO Blender回放的原frame42/43/45/47，读取 fit-only `partial-contour-alpha/input/input_bundle.json` 中 Patch/Crack 共8张RGB。没有加载任何留出 RGB、留出像素边界、源动态网格/状态/缺陷坐标/贴图/对象ID。早期阅读既有交接 catalog 时看过留出相机元数据；这些记录没有进入 tracker，也未打开其图像。完整访问清单逐文件记录在最终 `tracks.json`。

| 最终 v6 | 首次计划点 | 合法锚点 | 后续计划/合法 | frame43/45/47 合法 | 四时刻完整固定track |
|---|---:|---:|---:|---|---:|
| Patch | 12 | 7 | 36 / 12 | 6 / 5 / 1 | 1 |
| Crack | 10 | 10 | 30 / 17 | 8 / 5 / 4 | 4 |
| 合计 | 22 | 17 | 66 / 29 | 14 / 10 / 5 | 5 |

全部22点×4时刻=88个计划观测保留；46个 `RGB_TRACK_OBSERVED` 包含17个锚点，不能把它们说成46个非锚验证。其余为10竞争峰、7歧义和25身份已终止。实际已接受 target 的最大FB误差为Patch .196px、Crack .756px；最小NCC为 .970/.982；最小对比分别25.71/30.89灰度；最小竞争比2.038/2.202。条件sigma最大 .090/.123px，不是独立原像素对应误差。

Patch 后期重复矩形边缘和变形后的局部外观仍不能唯一确认，多数拒绝；Crack中段存在直边沿线滑动/重复外观，不能将所有中段点当有独立物质身份。原RGB约束的合法子集可用于受控候选，但完整物质对应要由未参与调参的独立留出检验。此前实际Patch/Crack各0/7的几何对应负结果没有被这里覆盖；第4项是否改善以主报告新recon的独立评分为准。

v1–v5中间输出全部保留；每个版本在匹配前记录固定参数，v1源码按其冻结SHA字节恢复并核对，v2–v6保存实际frozen-source。v1的3/66合法非锚观测在最终v6变为29/66；开发过程中只修复RGB运动/描述子坐标合同、人工warp边缘金字塔问题与NCC舍入，没有放宽门槛。机器汇总见 `tracking-development-summary.json`。

## 未知域与验证边界

对16张fit mask独立统计的未知域总面积212,784像素，测得occupancy与unknown交集51,271像素；这只是2D已声明域面积，不是源可见叶片分母，不能换算成几何恢复比例。逐原frame/t/camera/mask SHA和known/unknown面积见 `unknown-domain-audit.json`。新增helper保留所有occupancy字节，完整实例仍延续 `FAILED_INPUT_INSTANCE_GATE`；本子任务没有新增完整实例验收或叶片实例身份跟踪证据。

7项新通用回归通过：已知RGB平移误差最大<.15px，已知RGB旋转/剪切误差最大<.35px，重复图案拒绝、未知域失效后不重新获取、未知域不修改occupancy、跨相机名不能构造对应，以及声明路径逃逸/外部symlink/真值symlink/forbidden目录/角色错配拒绝。另有既有6项RGB mask回归通过；早期6项tracker日志在本目录，最终7项实际工具执行通过，机器审查记于 `v7-path-hardening-audit.json`。这些是开发可控图像回归，不是实际独立留出、现场实测、自动缺陷分类或缺陷深度恢复。

最终生产v7补充了读取前的文件角色allowlist、相对路径与解析后bundle containment、evaluation/truth/object_id/dynamic-source等路径拒绝、扩展名角色校验与显式heldout split拒绝。合法目标可用的路径完整记录SHA；此校验是声明文件入口合同，不是进程级文件系统sandbox，也不证明文件在此前采集阶段不存在未声明的数据依赖。v7与v6全部tracks.json、protocol.json字节相同，固定点/UV/quality/status完全不变；核心跟踪AST SHA均 `517cc6756287e80eb2b5aabfef99d95a2fede6dc5f519a2ba17b7d563e294e10`。

同一8张RGB/22点/88计划观测，在独立新进程中测得RGB预处理v1墙钟 .44s、RSS138,641,408字节；v6墙钟7.80s、RSS152,338,432字节，增加7.36s与9.88% RSS。最终v6重跑的tracks.json字节SHA与正式冻结完全相同。这里是RGB观测提取成本，不能替代几何候选或纹理融合同负载总耗时。没有声称本改动加速。

## 最终冻结与复现

- tracks SHA：`259c7a46bf6c998fa5271ca9db1edbaf80f68d81c2e5fbe27446be603f6477f3`
- protocol SHA：`fd82db1c2a54071660e3e8ca5e825c45b097626c770578d59d2f67f5bc9841d3`
- surface_tracks.py 最终v7 SHA：`7897b6773f1071af2046c688a1fc1e8b6e5706092fc93e169e591a9cacfff59d`；v6观测冻结源码SHA `65da6e741ec7962cb808c49790245aed85f611488648b35e745a7706f6724a08` 保留。
- rgb_masks.py SHA：`df622e7019f8d0e273e7d2a09c0812e18dfefddc7817e6064ab6e08cb2a7fa75`

在实际Git根目录使用已有 `blade_recon/.venv-trial/bin/python`：

```sh
blade_recon/.venv-trial/bin/python outputs/blade-recon-v02-stage4-20261007-a01/observations/fit-fixed-anchor-v7/frozen-source/surface_tracks.py --bundle outputs/blade-recon-v02-prereq-20261007-a01/masks/partial-contour-alpha/input/input_bundle.json --cameras Patch Crack --out <新的观测输出目录>
blade_recon/.venv-trial/bin/python blade_recon0.2/tests/test_surface_tracks.py
blade_recon/.venv-trial/bin/python blade_recon0.2/tests/test_rgb_masks.py
```

`fit-fixed-anchor-v6` 中每时刻RGB图只叠加原位置角点ID；绿为质量门接受，红为本时刻拒绝。拒绝点的诊断UV不进入solver，原RGB未增强。主流程后续仍需独立判断新几何是否减少原像素Patch/Crack误差，并保留失效、不参与求解的留出分母和完整实例失败。
