# 源码与项目资源

项目入口为 [snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)，当前源码分支为 [`wind-farm-rl-all-work`](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work)。[项目资源 Release](https://github.com/snode11/wind-farm-blender/releases/tag/project-resources-v1) 提供大型保存数据、研究证据、便携依赖源码与演示材料；它不是新版 Blender 扩展。现有公开扩展 ZIP、日常安装和源码的身份见[发布状态与验证范围](blender/发布状态与验证范围.md)。

## 文件用途与取得方式

| 内容 | 位置与分发 |
| --- | --- |
| 当前实现、测试、配置与文档 | Git 分支中的 `wfrl/`、`scripts/`、`tests/`、`blender_frontend/`、`blade_recon/`、`blade_recon0.2/`、`projects/` 等 |
| 普通 Python 包 | `wfrl` 模块及 `wfrl/turbines/` 内置 YAML 机型定义，核心依赖为 PyYAML；不包含 WFCRL、求解器、仓库级场景或 Blender 扩展 ZIP |
| 扩展正式载荷 | `blender_frontend/wfrl_blender/assets/` 与 `_vendor/` 随 Git 提供；构建不需要重新运行 FAST.Farm |
| normal/close 独立净空演示 | `results/lidar/packages/normal-v1.1/`、`close-v1.1/` 与 `dist/lidar-delivery.json` 随 Git 提供；与柔性 MAPPO 数据分别使用 |
| 后端 Replay 示例权重 | `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt` 随 Git 提供；保存权重不代表训练已重现 |
| NREL 几何导出输入 | `projects/nrel5mw-reference/template/` 保存正式资产实际读取的 19 份模板输入；导出器默认使用此位置 |
| WFCRL 源码与完整模板 | `wfcrl-dependency` 资源包恢复到 `wfcrl-env/`，保留 Apache-2.0 许可证；不包含嵌套 `.git`、环境或编译 DLL |
| GW184 几何、运动、相机与来源 | `projects/gw184-single/` 随 Git 提供，包含两份 DTU 原始参考输入；官方来源与固定摘要见[来源说明](../projects/gw184-single/sources/README.md) |
| 单叶片视频与动态研究 | `nrel-video`、`nrel-dynamic` 资源包含保存输入、数值结果、评分及独立查看器；`runtime-inputs` 提供试片与编码适配器读取的原始 PNG 采集输入 |
| 三相机重建与同源纹理 | `blade-reconstruction`、`same-source` 资源包含合成输入、保存重建及纹理再生成所需数据；扩展日常运行直接使用正式载荷 |
| 表面材料点与雷达研究 | `blade-surface`、`lidar-research` 资源包保留正负结果、原始观测、数值数组与独立评分；合成真值与求解输入保持各自角色 |
| 相机视频运行输入 | `camera-video` 提供演示视频与保存清单；`runtime-inputs` 提供帧映射、相机几何数组与 MediaMTX 配置；完整包的输入限制见下文 |
| 0.3.30 扩展与证据 | [v0.3.30 Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.30)提供 [wfrl_blender-0.3.30.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.30/wfrl_blender-0.3.30.zip)、inventory 与 SHA-256；184 文件、99,091,527 字节，身份见[交付身份](../publication/blender-0.3.30.json)，[当前载荷验证](../publication/blender-0.3.30/verification.json)标识 475 项选定宿主、隔离安装、相机连续重开与归属窗口、手动变桨及非媒体视频控制器跨进程恢复范围。隔离 profile 与生成测试场景属于可清理中间产物，验证报告保留运行时来源；报告中的运行路径标识证据来源 |
| 软件验证与展示 | Git 保留小型判定、来源与测试输入；`frontend-evidence` 资源包提供所选可见窗口证据。演示视频和两份正式演示文稿独立提供 |

资源附件的完整文件路径、字节数与 SHA-256 见 [resources.json](../publication/resources.json)。同专题的编号附件是独立归档，须全部取得后才具备清单声明的完整输入。附件文件名不改变内部项目相对路径。最后解压 `zz-frozen-code-originals.tar.gz`，以原字节实验脚本满足冻结代码摘要；这些脚本可能保存生产机来源位置，应显式适配调用环境，不能把可携带报告副本当作原字节源码。

## 恢复与使用

克隆源码分支，在仓库根目录执行以下命令，逐个恢复需要的资源附件：

```sh
git clone --branch wind-farm-rl-all-work https://github.com/snode11/wind-farm-blender.git
cd wind-farm-blender
tar -xzf /path/to/resource-part.tar.gz
```

解压目标必须是该源码仓库根目录。资源文件不会安装 Python 环境、覆盖日常 Blender 扩展或启动求解器。WFCRL 源码恢复后，可在自行准备的 Python 环境中执行 `python -m pip install -e ./wfcrl-env`。根项目普通安装使用 `python -m pip install .`，源码开发使用 `python -m pip install -e .`；两者以及构建的 wheel 均提供内置机型 YAML，并按核心依赖声明安装 PyYAML。训练仍需 `train` 附加组和匹配的后端环境，安装范围与命令模板见[本地配置](setup/setup_local.md#2-python-环境floris-与算法开发)。

FAST.Farm、OpenFAST、TurbSim、MPI 与控制器须按目标平台单独安装或编译。求解脚本接受 `WFCRL_FASTFARM_EXECUTABLE`、`WFCRL_OPENFAST_EXECUTABLE`、`WFCRL_TURBSIM_EXECUTABLE`，否则在 `PATH` 中寻找可执行文件。Python 包安装和后端源码变更不更新公开扩展附件或本机已安装扩展；Interactive、Formal Training 与 SB3 的入口及统计定义见[算法配置](training/MAPPO_SETUP.md)。

仅离线使用公开扩展时，按[安装说明](blender/INSTALL.md)安装专用 ZIP 即可，无需恢复研究数据和后端环境。源码扩展可在新输出目录构建；源码发布不证明新生成 ZIP 已通过公开安装版的所有窗口和平台验收。

## 证据身份与限制

面向阅读的文档与报告公开副本对机器专属路径、环境位置及个人标识做了可携带化处理；原始文件保留在本地。绑定内容摘要的正式运行资产、normal/close 清单与冻结科学输入保留原字节和来源定位字符串，避免破坏严格加载、同源身份或复现合同；这些定位字符串不代表随包提供作者机器目录或环境。资源清单分别记录原始摘要和公开摘要，处理过的公开副本以公开摘要核对。原证据中记录的源文件／报告摘要仍标识原始对象，不能用来宣称去路径后的副本逐字相同。科学测量、阈值、失败状态与 `null` 未据此改写。

当前源码包含拒绝／非有限重建候选的历史与先验保护、启动器停止确认失败关闭、完整播放覆盖约束、单次数据校验复用、PNG 采集纹理分配控制和安全构建输出。[verification.json](../publication/verification.json)所标识源码快照的前端宿主检查为 726 项通过／3 项跳过；后端全套为 837 项通过／5 项跳过，并通过两项额外的已存在数据损坏检查；拒绝帧数值回归 5 项通过；GW184 使用匹配参考输入为 47 项通过／1 项跳过。命令、依赖环境、跳过条件和范围见 [verification.json](../publication/verification.json)。这些检查与正式安装包证据分别使用。

相机基础视频包的 1200 张 PNG 母版未分发，当前工作区也不具备这些母版，不能声明完整包校验通过。五分钟包的 `source/video.mp4` 未单独复制；其固定摘要与基础包 `video.mp4` 相同，完整校验前需将该现存基础视频恢复到嵌入来源位置。视频播放、帧映射与完整来源验证分别使用。

普通离线结果包不含原始 FAST.Farm VTP，Bridge 的原始叶尖几何回放仍需完整原始运行目录；缺失时明确拒绝，不能使用示意几何代替。材料点 F/G 实验的原始输入、冻结脚本及完整运行产物不在当前工作区和本资源分发中；当前报告不能支持其完整精确复现。CoTracker 的第三方源码和权重不随资源包分发，相关固定来源与许可见研究 provenance。研究精确重跑还需要匹配的外部依赖、求解器及实际输入；本资源分发不声明已经重跑。

几何／纹理仍为 `NOT_ACCEPTED`，0.2 mm 叶尖门槛与材料点研究保持 `FAILED_GATE`；雷达保持 `REVIEW_ONLY / PENDING_ACCEPTANCE`，动态研究保持 `DY1 NOT PASSED`。软件测试和保存场景不能替代物理精度、实物相机、长期运行或 Windows/Linux 验收。

## 不分发的本地内容

个人申请材料、第三方论文和设备手册、商业构想草稿、环境、机器偏好和凭证不纳入分发。缓存、打包副本、失效过程文件、旧候选稿和非正式侦察图像按可恢复方式存放在工作区之外；恢复映射与已有隔离区一并保全。有效输入、冻结方法、正负结果及验收证据保留在项目中。外层 `wfcrl-env-me/` 是独立 RL 源码树，不能作为重复目录删除，也不整体混入 Blender 项目；本项目实际读取的几何输入已有独立归属。资源范围不包括已移出当前工作区的历史产物。

来源与许可证按文件适用范围保留，见[第三方来源](../THIRD_PARTY.md)。根目录没有统一覆盖所有研究数据和参考资源的许可证，扩展 manifest 的 MIT 声明不替第三方资源重新授权。
