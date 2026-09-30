# GW184 单机启停三相机与缺陷编辑器 · 0.4.0

更新日期：2026-09-30。独立的 Blender 合成场景，包含固定三相机、六类叶片缺陷编辑器，以及原三路原始 MP4 管线；复用前端网格与针孔投影辅助函数。

**GW184 0.4.0 已作为独立运行 ZIP 发布；完整 320 秒三路视频仍未生成。** 给朋友的视频任务最终仅交付 `C1.mp4`、`C2.mp4`、`C3.mp4`，这项视频交付约束与供用户启动编辑器的运行 ZIP 分别处理。2026-09-30 的 18 张授权静态图已完成有限外观审核；它们不代替完整视频、拼接算法或第一阶段整体验收。

## 已发布运行包

从 [0.4.0 发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.4.0)下载 [gw184_three_camera_defects-0.4.0.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.4.0/gw184_three_camera_defects-0.4.0.zip)。需要 Blender 5.2+，将整个 `GW184-Three-Camera-0.4.0` 文件夹解压到有写入权限的位置：

- macOS：双击根目录的 `打开GW184.command`；默认查找 `/Applications/Blender.app`，其他位置可设置 `BLENDER_BIN`。
- Windows：双击根目录的 `Open-GW184.bat`；可用 `BLENDER_BIN` 指定 `blender.exe`。目前只有脚本静态检查，没有 Windows 实机验证。
- 任意平台：在解压后的运行包根目录执行 `blender --factory-startup --python scripts/blender/gw184_defects.py -- --mode edit --all-presets`。

这是解压后运行的项目，**不要通过 Blender 的 Install from Disk 安装**；不依赖 NREL 扩展、Python 后端、OpenFAST、FAST.Farm 或 Bridge。NREL 5MW 柔性回放和双束净空仍使用独立的 `wfrl_blender-0.3.12.zip`，旧版本保留。安装和操作详见 [GW184 使用说明](../../docs/blender/GW184三相机与缺陷编辑器.md)。

模型为 184 m 参考直径、90 m 叶片、3 m 静态预弯的合成刚性几何：借用 DTU 10MW 的弦长、厚度、扭角分布和固定 FFA 翼型，不是制造商 CAD，也不是放大后的 NREL 柔性模型。六类缺陷不耦合结构或气动求解。

本 GitHub 项目目录同步的是说明文档；实现源码、配置、参考资源和测试随运行 ZIP 提供。GitHub 自动生成的 Source code 归档不能替代运行 ZIP。下文的脚本和配置命令适用于完整运行包根目录或具备匹配实现的工作区，不能在仅含公开文档的仓库副本中直接运行。

当前参数和证据边界见 [requirements.md](requirements.md)。工作区原三视频管线检查保存在 `outputs/gw184-single/preflight-20260929/ready/`，汇总为工作区文件 `outputs/gw184-single/preflight-20260929/检查说明.md`，不随发布 ZIP 或公开文档交付。旧 `review/`、`scene-v1/` 和两组历史短片不代表当前模型；编辑器开发后旧 preflight 的源码签名已过期，后续运行原视频管线须重新生成无渲染检查。

## 不渲染的准备与检查

从完整运行包根目录或匹配源码工作区根目录运行（macOS）：

```sh
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
"$BLENDER" --background --factory-startup --python-exit-code 1 \
  --python scripts/blender/gw184_single.py -- \
  --mode preflight --output outputs/gw184-single/my-new-preflight
```

`preflight` 构建带预弯和缺陷的模型，检查实际网格、投影、采样遮挡/相交和全程动画，保存并重新打开 `.blend` 校验。不会调用渲染器或视频编码器。`prepare` 只构建、烘焙并保存场景；`audit` 只做覆盖采样、记录参数及参考场景，也不渲染。

几何、旧视频管线默认缺陷、时间线和相机配置分别在 `projects/gw184-single/geometry/target.json`、`projects/gw184-single/geometry/defects.json`、`projects/gw184-single/motion/timeline.json` 和 `projects/gw184-single/cameras/layout.json`。翼型来源已经固定到 Git 提交及内容哈希，运行 ZIP 保留来源与许可。原视频管线默认的两处缺陷与编辑器六类预设是不同配置，不能混作相同的场景快照。

## 验证

0.4.0 实际 ZIP 在另一含空格的目录解压检查：47 项宿主测试通过，1 项因未提供历史修改前源码目录跳过；macOS Blender 5.2.1 LTS 的六类型后台检查 126 项通过，生成 36 条三相机 G 记录，真实渲染器调用为零。根目录 macOS 启动器的独立窗口检查通过，确认从包内模块加载、六类示例、C1/C2/C3、编辑面板、时刻和近景切换，未加载已安装 NREL 扩展。Windows/Linux 实机、完整片段帧率和现场真实性未验证。完整范围见 [发布状态与验证范围](../../docs/blender/发布状态与验证范围.md)。

下面是原视频管线的检查命令，使用自己刚生成的 preflight 路径；不要直接使用工作区历史结果：

```sh
python3 -m unittest discover -s projects/gw184-single/tests -p 'test_model.py' -v
"$BLENDER" --background --factory-startup --python-exit-code 1 \
  --python projects/gw184-single/tests/blender_regression.py -- \
  --preflight-dir outputs/gw184-single/my-new-preflight
```

第二项核对当前源码/配置哈希和保存场景，验证全部渲染模式在未授权时都会提前拒绝，并检查三叶片启停变桨、反向寻址、固定相机及缺陷归属。源码或配置改变后，须先生成新的 preflight，不接受旧场景的历史 PASS。

`tests/cli_resume_regression.py` 会实际渲染，因此不属于当前检查；它要求明确传入 `--render-authorized --output <新目录>`。本次没有运行它，不能把旧版本的编码回归当作新模型的成片验收。

## 后续经授权的渲染

`stills`、`clip`、`full` 均默认禁止渲染，只有用户明确授权后操作者才可传入 `--render-authorized`。`full` 还需 `--preview-approved`，记录输入试片已被查看的操作者声明。开关本身不是用户授权，也不是算法验收。

输出必须选择空的新目录，`job.json` 锁定配置、缺陷、项目源代码、共享网格/投影函数、参考来源和 Blender 版本；变更后拒绝混用旧帧。PNG 原子写入；`--max-frames N` 限制每批同步帧数，状态为 partial，直到三路完整帧齐备才编码并核对帧数、分辨率和时长。默认 Eevee 64 样本，4K/30 fps，320 s 每路 9600 帧；FFmpeg/FFprobe 用于后续编码检查。

保存场景使用实体视口供建模检查，不自动开始动画渲染。内部 `metadata/` 和检查报告留在项目中，不打包交付给朋友。

独立刚性缺陷编辑器的启动、配置、六类形态、G 扫描和按需 A 图入口见 [DEFECT_EDITOR.md](DEFECT_EDITOR.md)。v1.1 是开发规格版本，0.4.0 是已发布运行包版本，二者不能互换。发布包不含视频，第一阶段整体验收及原三份 MP4 的后续渲染仍待完成。
