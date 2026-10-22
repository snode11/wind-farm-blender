# Wind Farm Blender

面向风场仿真观察、相机数据制作和叶片重建研究的 Blender 工作台。项目包含两个独立产品，均要求 **Blender 5.2+**。

| 产品 | 公开版本 | 用途与使用方式 |
| --- | --- | --- |
| NREL 5MW / WFRL 前端 | **0.3.24** | 独立风机手动控制、姿态预设与 JSON、三机柔性回放、几何／同源纹理分屏、六类合成缺陷、三相机与双束／TLS；[下载扩展 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.24/wfrl_blender-0.3.24.zip)，使用 Install from Disk 安装并重启 |
| GW184 三相机与叶片缺陷 | **0.4.0** | GW184 尺寸合成刚性模型、固定三相机与六类缺陷；[下载独立运行 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.4.0/gw184_three_camera_defects-0.4.0.zip)，完整解压后启动 |

## 当前身份与安装来源

项目源码入口为 [`wind-farm-rl-all-work`](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work)，包含当前前端、后端、重建研究、测试和配套文档。NREL 的 [v0.3.24 Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.24) 提供专用扩展 ZIP；安装时使用 `wfrl_blender-0.3.24.zip`。发布源码标签 `v0.3.24` 指向 `844b1de6a04a612b72b244d42749ae5ba6ee9213`。源码提交、构建 ZIP 与本机安装分别识别，GitHub 自动 Source code 不作为扩展安装包。

开发机日常扩展 `bl_ext.user_default.wfrl_blender` 使用 **0.3.28 本地安装包**，包含 NREL／rootkink 叶片外形选择、[T1/T2/T3 Down 机舱下视](docs/blender/T1Down机舱下视.md)及当前工作区的相机与播放修复，安装身份与验证范围见[安装核对](publication/daily-installation.json)。0.3.28 尚未公开发布；公开附件仍为 0.3.24，身份见[交付身份](publication/blender-0.3.24.json)。项目启动器、重建求解器和构建工具使用独立项目源码，不随扩展 ZIP 安装。

| 对象 | manifest | 身份 |
| --- | --- | --- |
| 公开 NREL ZIP | `0.3.24` | 180 文件；上传源包的已有 SHA-256、源码提交及证据归属见[交付身份](publication/blender-0.3.24.json)与[发布状态](docs/blender/发布状态与验证范围.md) |
| 当前开发机日常安装对应 ZIP | `0.3.28` | `outputs/frontend-stability-fixes/validation/package/wfrl_blender-0.3.28.zip`（本地文件；[逐文件清单](outputs/frontend-stability-fixes/validation/package/wfrl_blender-0.3.28.inventory.json)），181 文件；SHA-256 `b15c491db68fda6a2bdfce4228d5db040fa6ae0f0a69f886935ab0af9b775fda`；[安装身份](publication/daily-installation.json) |

身份、[发布核对](docs/blender/发布状态与验证范围.md#软件验证的适用对象)与[当前安装核对](publication/daily-installation.json)的适用范围见[发布状态与验证范围](docs/blender/发布状态与验证范围.md)。同一版本号不证明内容相同；升级后完全重启 Blender。

## 主要能力

- **风机手动控制**：**N → 风机控制 → 创建 / 进入手动场景** 提供 T1/T2/T3 或全部风机的朝向、统一／分别变桨、RPM、叶轮角度与运行／暂停／停转，支持朝下／侧向／顺桨预设、相对角度微调、三机参数概览、JSON 姿态保存／选择性载入及撤销重做。批量启用分别变桨保留已启用机组的独立桨距。使用独立刚性场景和环境，保留原 demo；不产生功率、载荷、尾流或测量结果。[操作说明](docs/blender/风机手动控制.md)。
- **离线物理结果回放**：三台 NREL 5 MW 风机、九片叶片及柔性塔架，播放 117–177 s 的 60 秒 MAPPO 保存记录，提供偏航、功率、转矩、挠度和净空对照。离线观看无需 Python 后端、MPI、FAST.Farm 或网络。
- **叶片外形（本地 0.3.28）**：MAPPO 侧栏可选择默认 NREL 或合成的 rootkink 外形；切换保留时刻、所选机组和相机设置，继续使用 NREL 保存运动与读数。[操作与使用边界](前端readme.md#3-时间回放与观察视角)。
- **几何／同源纹理分屏**：通过 **N → MAPPO → 打开几何重建示例…** 进入两侧完整风机，在几何与同源纹理间切换。共享播放、单帧步进与暂停；人工观察点仅定位一次，播放保留手动缩放和绕转，保存重开恢复模式与视图。
- **三相机与图像采集**：共盒 C1/C2/C3、整盒安装与默认复位，支持 2×2、三列、独立单路、单路放大和同刻／时间序列 PNG。第四格是静态安装示意；采集使用独立原始画质设置。
- **合成缺陷编辑**：NREL 九片叶片上的六类缺陷支持预览、确认／取消、健康对照、撤销重做、JSON 和 `.blend` 保存；外观随保存的柔性运动，不改变结构与气动物理。
- **雷达研究结果**：默认 `hub-axis.v1` 与显式选择的 `hub-tls.v1` 共用 40 Hz 源，S2/S3 配对和独立 S1 报警分别显示；保留旧 B2 入口与无效／过期状态。
- **后端操作**：配置完整环境后可连接 Bridge，使用 Replay 推理、Interactive 训练或 Formal Training。扩展 ZIP 不包含后端服务、MPI 或求解器。

## 快速开始

1. 下载 NREL 的专用扩展 ZIP，保留压缩格式。
2. 在 Blender 的 **Edit → Preferences → Get Extensions → Install from Disk** 安装并启用 WFRL Blender，完全退出后重启。
3. 按 **N → MAPPO → 打开几何重建示例…**，用模式按钮切换几何与同源纹理；当前文件未保存时按 Blender 提示处理。
4. 需要普通三机回放时，新建 General 场景并点击 **加载 MAPPO · 60 秒**；在 **View → 三相机 → 三路对照** 观察相机，在 **恢复默认三相机** 恢复默认盒体和三路参数。

GW184 使用[独立包操作说明](docs/blender/GW184三相机与缺陷编辑器.md)，不通过扩展安装。

## 源码与运行资源

当前源码、测试、研究工具与文档使用 [`wind-farm-rl-all-work`](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work) 分支。运行资源与证据分别随 Git 或 [project-resources-v1](https://github.com/snode11/wind-farm-blender/releases/tag/project-resources-v1) 提供，具体位置及取得方式见[分发说明](docs/PUBLICATION.md)。NREL 离线扩展构建所需正式载荷随 Git 提供；其他入口按需恢复对应资源。

| 用途 | 必须保留的内容 |
| --- | --- |
| 扩展构建与离线回放 | `blender_frontend/wfrl_blender/assets/`、`_vendor/`，以及构建使用的规范 `wfrl/` 源码 |
| 独立净空演示 | `dist/lidar-delivery.json` 与 `results/lidar/packages/normal-v1.1/`、`close-v1.1/` 的完整数据包 |
| 后端 Replay | 兼容后端环境，以及 `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt` |
| GW184 项目 | `projects/gw184-single/` 的 geometry、motion、cameras 与 sources；两份 DTU 原始输入随 Git 提供，官方来源与固定摘要见[来源说明](projects/gw184-single/sources/README.md) |
| 验证与研究复查 | 分发清单标识的原始测量、摘要、日志和图像；每项证据只适用于其记录的输入与载荷 |

公开 0.3.24 扩展包含独立风机手动控制、相机异常资源清理、同源重建稳定刷新、完整播放覆盖和循环判定、回放包单次校验复用及 PNG 采集纹理分配控制。拒绝帧与非有限重建结果的历史／先验保护、启动器停止确认及构建输出保护属于项目侧工具，按[源码使用说明](docs/blender/INSTALL.md#从项目源码构建)独立取得；源码同步与公开发布均不会替换本机日常安装。

## 验证与限制

源码检查的命令、依赖、跳过条件与记录范围见[分发说明](docs/PUBLICATION.md#证据身份与限制)及其对应源码快照；各项结果只适用于记录的源码与输入，不作为当前公开 ZIP 或日常安装的重复验收。

公开 0.3.24 ZIP 的附件、上传源包与源码身份见[交付身份](publication/blender-0.3.24.json)。当前本地 0.3.28 安装包的身份与验证范围见[安装核对](publication/daily-installation.json)；姿态合同、原生后台与短时窗口证据的载荷归属见[发布状态](docs/blender/发布状态与验证范围.md#软件验证的适用对象)。各项结果仅适用于记录的载荷、环境和范围。公开交付没有新增测试、安装或窗口验收；公开 ZIP 与标签源码复建的一致性、长期运行、完整片段性能和科学精度未验收。

同源纹理是 **SAME_SOURCE_SIMULATION**：601 个 10 Hz 保存样本对应 117–177 s，60 Hz 时间轴每 6 帧保持一个样本；独立 `synth_tex` 是另一来源的 120 样本、0–11.9 s。放大与增强不会补出未观测表面。两者的几何／纹理仍为 **NOT_ACCEPTED**，图集模糊、条带、接缝和叶尖错贴限制保留。

外部材料点研究候选为 Patch **2/8**、Crack **7/8**、跨相机接受 **0/264**，仍为 **FAILED_GATE / NOT_ACCEPTED**，`same_material_error_m` 与 `primary_gains` 为 `null`；正式 601 样本和图集未替换。材料点 F/G 的原始输入、冻结脚本和完整运行产物未分发，报告不能支持完整精确复现。原 0.2 mm 叶尖精度门仍为 **FAILED_GATE**；雷达仍为 **REVIEW_ONLY / PENDING_ACCEPTANCE**。

Windows/Linux、稳定 30／60 FPS、长期运行、实物相机标定、自动缺陷检测、现场精度及安全保护均未验收。三相机参考姿态的重叠不等于全周期无遮挡或拼接成功；合成缺陷几何可见不证明图片可辨识。

## 使用与资料

- [安装说明](docs/blender/INSTALL.md) · [前端操作](前端readme.md) · [完整用户手册](docs/blender/用户使用手册.md)
- [功能总览与规划](docs/blender/Blender前端功能总览与规划.md) · [当前待实施要求](前端更新计划.md)
- [实现与维护](blender_frontend/README.md) · [三相机](docs/blender/T1三相机使用说明.md) · [NREL 缺陷](docs/blender/NREL缺陷编辑器.md) · [Blade Recon](docs/blender/BladeRecon可视化使用说明.md)
- [包与证据索引](docs/blender/README.md) · [雷达算法](wfrl/lidar/README.md) · [材料点研究结果](blade_recon0.2/docs/材料点对应实验报告_2026-10-11.md)
