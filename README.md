# Wind Farm Blender

面向风场仿真观察、相机数据制作和叶片重建研究的 Blender 工作台。项目包含两个独立产品，均要求 **Blender 5.2+**。

| 产品 | 公开版本 | 用途与使用方式 |
| --- | --- | --- |
| NREL 5MW / WFRL 前端 | **0.3.40** | NREL／rootkink 叶片外形、T1/T2/T3 Down、独立风机手动控制、姿态预设与 JSON、三机柔性回放、几何／同源纹理分屏、六类合成缺陷、三相机与双束／TLS；[下载扩展 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.40/wfrl_blender-0.3.40.zip)，使用 Install from Disk 安装并重启 |
| GW184 三相机与叶片缺陷 | **0.4.0** | GW184 尺寸合成刚性模型、固定三相机与六类缺陷；[下载独立运行 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.4.0/gw184_three_camera_defects-0.4.0.zip)，完整解压后启动 |

## 当前身份与安装来源

GitHub 默认分支为 [`main`](https://github.com/snode11/wind-farm-blender/tree/main)；完整项目源码分支为 [`wind-farm-rl-all-work`](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work)，包含前端、后端、重建研究、测试和配套文档。NREL 的 [v0.3.40 Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.40) 提供专用扩展 ZIP；安装使用 `wfrl_blender-0.3.40.zip`，发布源码使用 `v0.3.40` 标签。源码提交、构建 ZIP 与本机安装分别识别，GitHub 自动 Source code 用于源码查看与构建。

开发机日常扩展模块为 `bl_ext.user_default.wfrl_blender`。公开包、日常安装的版本与载荷分别取自下列身份记录；源码工作区的模块职责见[实现与维护](blender_frontend/README.md)。项目启动器、重建求解器和构建工具使用项目源码。

0.3.40 配套源码提供[停止重试与本机进程监管](blender_frontend/README.md#55-实时连接与控制生命周期)、[实验配对／价值归一化／GRU／续训](docs/training/MAPPO_SETUP.md#fastfarm-实验身份与-gru)及[同模型评分](blade_recon0.2/README.md#评分模型与样本身份)。扩展 ZIP 包含命令水位恢复、相机采集事务结果、后台视频取消和相机几何缓存；Bridge、训练、评分和启动器通过完整源码归档提供。载荷按 `build-info.json`、inventory 和[交付身份](publication/blender-0.3.40.json)核对。

<!-- release-identity:start -->
| 对象 | 版本 | 身份依据 |
| --- | --- | --- |
| NREL 公开安装包 | **0.3.40** | [wfrl_blender-0.3.40.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.40/wfrl_blender-0.3.40.zip)；195 个文件；[交付身份](publication/blender-0.3.40.json) |
| 开发机日常安装 | **0.3.40** | 独立载荷，按安装记录核对；201 个文件、95 个新进程模块已核对；[安装身份](publication/daily-installation.json) |
<!-- release-identity:end -->

身份、[发布核对](docs/blender/发布状态与验证范围.md#软件验证的适用对象)与[当前安装核对](publication/daily-installation.json)的适用范围见[发布状态与验证范围](docs/blender/发布状态与验证范围.md)。同一版本号不证明内容相同；升级后完全重启 Blender。

0.3.40 发布源码与扩展包含后台 PNG 完成确认、回合曲线边界、连接恢复及三相机共享资源保护；配套启动器与 Bridge 通过源码归档提供。合同与保存证据见[源码软件合同与验证](docs/blender/发布状态与验证范围.md#源码软件合同与验证)。日常安装为 0.3.40 本地源码构建，提供三机三叶响应统计与曲线；该载荷未公开发布，文件摘要和实际检查范围见安装身份记录。

公开扩展在 **N → WFRL** 提供回放、相机、手动、导出和后端页面，保留原页签。主入口按实际数据与播放归属显示状态，离线保存结果、独立手动刚性运动和后端运行分别说明。公开包包含主入口、录制与后台 PNG 渲染；用法见[前端操作](前端readme.md#wfrl-主入口)。工作区的源码变动不自动替换公开附件或日常安装。

公开 0.3.40 前端按网格与数值消费者选择形变计算范围，保留隐藏 T1 的结构叶尖测量和共享塔架运动，并复用已校验遥测数组。后端的完整功率校验、断连期间的生命周期推进与进度合并、Interactive 联合动作 PPO 使用项目 Python 源码，说明见[前端与后端接口](blender_frontend/README.md#55-实时连接与控制生命周期)及[训练配置](docs/training/MAPPO_SETUP.md)。普通 Python 包的机型资源与依赖见[本地配置](docs/setup/setup_local.md#2-macos-完整-python-环境)。

## 主要能力

- **风机手动控制**：**N → 风机控制 → 创建 / 进入手动场景** 提供 T1/T2/T3 或全部风机的朝向、统一／分别变桨、RPM、叶轮角度与运行／暂停／停转，支持朝下／侧向／顺桨预设、相对角度微调、三机参数概览、JSON 姿态保存／选择性载入及撤销重做。批量启用分别变桨保留已启用机组的独立桨距。使用独立刚性场景和环境，保留原 demo；不产生功率、载荷、尾流或测量结果。[操作说明](docs/blender/风机手动控制.md)。
- **手动试验与视频**：公开扩展与配套源码提供 rootkink 手动场景的 T1 恒速变桨、独立 T1 Down 和 1920×1080、25 fps H.264 视频生成；视频后台需要配套项目源码，日常扩展按其独立安装身份识别。生成命令默认等待最终结果，显式 `--detach` 可脱离运行；持久化作业支持查询、取消和完整母版校验后的编码重试，发布前预检目标文件系统并拒绝覆盖已有输出。[生成、监管、重试与验证范围](docs/blender/手动场景研究接口.md#导出变桨视频)。
- **离线物理结果回放**：三台 NREL 5 MW 风机、九片叶片及柔性塔架，播放 117–177 s 的 60 秒 MAPPO 保存记录，提供偏航、功率、转矩、挠度和净空对照。离线观看无需 Python 后端、MPI、FAST.Farm 或网络。
- **叶片外形**：MAPPO 侧栏可选择默认 NREL 或合成的 rootkink 外形；切换保留时刻、所选机组和相机设置，继续使用 NREL 保存运动与读数。[操作与使用边界](前端readme.md#3-时间回放与观察视角)。
- **几何／同源纹理分屏**：通过 **N → MAPPO → 打开几何重建示例…** 进入两侧完整风机，在几何与同源纹理间切换。共享播放、单帧步进与暂停；人工观察点仅定位一次，播放保留手动缩放和绕转，保存重开恢复模式与视图。
- **相机观察**：观察视口不显示画幅外遮罩或额外画幅边界；姿态和采集元数据变化复用代理与手动取景，光学变化按真实 FOV 校准。关闭并完成资源释放后停止观察定时器，重新打开恢复监听。
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

完整项目的 Python 环境统一使用根目录 [requirements.txt](requirements.txt)，包括 WFCRL、FLORIS、FAST.Farm Python 接口、训练、桌面 GUI 和相机/重建依赖。macOS 使用 Python 3.11，先恢复 WFCRL 资源并准备 OpenMPI；安装命令及 Blender、求解器与控制器要求见[完整环境配置](docs/setup/setup_local.md#2-macos-完整-python-环境)。

当前源码、测试、研究工具与文档使用 [`wind-farm-rl-all-work`](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work) 分支。运行资源与证据分别随 Git 或 [project-resources-v1](https://github.com/snode11/wind-farm-blender/releases/tag/project-resources-v1) 提供，具体位置及取得方式见[分发说明](docs/PUBLICATION.md)。NREL 离线扩展构建所需正式载荷随 Git 提供；其他入口按需恢复对应资源。

| 用途 | 必须保留的内容 |
| --- | --- |
| 扩展构建与离线回放 | `blender_frontend/wfrl_blender/assets/`、`_vendor/`，以及构建使用的规范 `wfrl/` 源码 |
| 手动 T1 Down 视频 | 完整工作区源码，以及 `blender_frontend/wfrl_blender/assets/` 内的 rootkink／NREL 几何、地形纹理与环境 HDR；[生成入口](scripts/blender/generate_manual_pitch_video.py)须指定 `--output`，不依赖已有输出。恢复既有作业及重试编码还需保留其 `job.json` 与完整作业数据目录 |
| 独立净空演示 | [交付清单](dist/lidar-delivery.json)指定 `results/lidar/packages/normal-v1.1/`、`close-v1.1/` 两个完整结果包；[专用启动入口](scripts/blender/打开净空雷达演示.command)使用项目源码，不安装扩展或启动求解器 |
| 后端 Replay | 兼容后端环境，以及 `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt` |
| GW184 项目 | `projects/gw184-single/` 的 geometry、motion、cameras 与 sources；两份 DTU 原始输入随 Git 提供，官方来源与固定摘要见[来源说明](projects/gw184-single/sources/README.md) |
| 验证与研究复查 | 分发清单标识的原始测量、摘要、日志和图像；每项证据只适用于其记录的输入与载荷 |

公开 0.3.40 扩展包含独立风机手动控制、相机异常资源清理、同源重建稳定刷新、完整播放覆盖和循环判定、回放包单次校验复用及 PNG 采集纹理分配控制。拒绝帧与非有限重建结果的历史／先验保护、启动器停止确认及构建输出保护属于项目侧工具，按[源码使用说明](docs/blender/INSTALL.md#从项目源码构建)独立取得；源码同步与公开发布均不会替换本机日常安装。

## 验证与限制

源码检查的命令、依赖、跳过条件与记录范围见[分发说明](docs/PUBLICATION.md#证据身份与限制)及其对应源码快照；各项结果只适用于记录的源码与输入，不作为当前公开 ZIP 或日常安装的重复验收。

公开 ZIP 的文件、源码标签和下载地址见[交付身份](publication/blender-0.3.40.json)；195 文件、99,156,317 字节，SHA-256 为 `f7907be0bd6737f0b0d8ddde8b856c151b6ebc7392ee1c796fc9252b806f018b`。ZIP 逐文件、隔离安装、新进程来源与相机后台结果，以及单独的源码脚本窗口结果见[验证范围](docs/blender/发布状态与验证范围.md#软件验证的适用对象)。[日常安装记录](publication/daily-installation.json)核对本地构建的 201 个文件、95 个新进程模块、持久启用、21 个控制相关文件及 6 个配置文件保护。日常安装的新进程与隔离安装后台检查、工作区源码的 GPU 采集窗口检查分别由记录中的证据标识；当前日常配置的可见窗口及真实输入未单独复测。完整视频、稳定 FPS、长期运行、其他平台、真实后端训练和科学精度未验收。

同源纹理是 **SAME_SOURCE_SIMULATION**：601 个 10 Hz 保存样本对应 117–177 s，60 Hz 时间轴每 6 帧保持一个样本；独立 `synth_tex` 是另一来源的 120 样本、0–11.9 s。放大与增强不会补出未观测表面。两者的几何／纹理仍为 **NOT_ACCEPTED**，图集模糊、条带、接缝和叶尖错贴限制保留。

外部材料点研究候选为 Patch **2/8**、Crack **7/8**、跨相机接受 **0/264**，仍为 **FAILED_GATE / NOT_ACCEPTED**，`same_material_error_m` 与 `primary_gains` 为 `null`；正式 601 样本和图集未替换。材料点 F/G 的原始输入、冻结脚本和完整运行产物未分发，报告不能支持完整精确复现。叶尖显示精度按[工作区源码与安装载荷](docs/blender/发布状态与验证范围.md#研究与物理边界)分别判定；工作区保存数据对照通过 0.2 mm／5 mm 显示门槛，本地 0.3.36 安装版另有 45 帧抽样记录；发布独立复验为 NOT_RUN，抽样不代表完整时间轴或科学精度验收。雷达仍为 **REVIEW_ONLY / PENDING_ACCEPTANCE**。

Windows/Linux、稳定 30／60 FPS、长期运行、实物相机标定、自动缺陷检测、现场精度及安全保护均未验收。三相机参考姿态的重叠不等于全周期无遮挡或拼接成功；合成缺陷几何可见不证明图片可辨识。

## 使用与资料

- [安装说明](docs/blender/INSTALL.md) · [前端操作](前端readme.md) · [完整用户手册](docs/blender/用户使用手册.md)
- [功能总览与规划](docs/blender/Blender前端功能总览与规划.md) · [当前待实施要求](前端更新计划.md)
- [实现与维护](blender_frontend/README.md) · [三相机](docs/blender/T1三相机使用说明.md) · [NREL 缺陷](docs/blender/NREL缺陷编辑器.md) · [Blade Recon](docs/blender/BladeRecon可视化使用说明.md)
- [包与证据索引](docs/blender/README.md) · [雷达算法](wfrl/lidar/README.md) · [材料点研究结果](blade_recon0.2/docs/材料点对应实验报告_2026-10-11.md)
