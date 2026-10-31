# 跨平台协作开发

发布版本为 **0.3.42.2**（GitHub 标签 `v0.3.42.2`，Blender manifest 为 `0.3.42+2`）。安装与公开载荷身份以 `publication/daily-installation.json` 和发布交付记录为准。

## 仓库与分支

仓库入口为 [snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)，本工作区对应 remote `blender-sync`。从 `main` 创建功能分支，通过 Pull Request 评审后合并，不直接推送主分支。工作前检查实际 Git 根和未提交修改，保留已有文件。

```bash
git clone https://github.com/snode11/wind-farm-blender.git
cd wind-farm-blender
git switch -c feature/your-feature
```

独立上传的源码分支可能没有共同祖先；整合时须按功能比较实际文件和接口，不能据分支名推定其包含当前主分支。

## 环境与构建

扩展兼容版本以 `blender_frontend/wfrl_blender/blender_manifest.toml` 为准；验证记录必须标明实际 Blender 版本和操作系统，不能把 macOS 的结果转记到 Windows 或 Linux。视频功能需要 FFmpeg／FFprobe，路径由扩展偏好设置和相应视频入口检查。

```bash
python3 scripts/blender/build_extension.py --output /absolute/path/new-package
```

构建目录须为新输出；构建 ZIP、源码、公开附件和已安装扩展分别识别。在 Blender 的 **Install from Disk** 中选择专用扩展 ZIP，更新后完全重启。实际加载来源可在 Blender Python 控制台核对：

```python
import sys
print([m.__file__ for n, m in sys.modules.items() if n.endswith('wfrl_blender')])
```

完整环境、构建身份与原生检查入口见[项目技能](../../.agents/skills/windfarm-blender-dev/SKILL.md)和[验证入口](../../.agents/skills/windfarm-blender-dev/references/verification.md)。

## 模块与接口

| 目录或模块 | 职责 |
| --- | --- |
| `blender_frontend/wfrl_blender/` | Blender 扩展及随包资源 |
| `manual_control.py`、`manual_operators.py`、`manual_rna.py` | 手动状态、操作器及属性注册 |
| `panels/manual_control.py` | 风机控制与 WFRL 手动页共用的基本面板 |
| `manual_down.py`、`manual_video.py` | Down 与独立视频研究接口 |
| `farm_flex.py` | 柔性数据包读取、身份验证与场景运行时 |
| `wfrl/lidar/` | 规范雷达计算、数据包与读取器源码 |
| `scripts/blender/` | 构建、检查及研究脚本 |

构建时从规范源码生成 `protocol.py` 和 `_vendor/lidar/`；源码运行与已构建扩展的依赖路径不同。不要手工修改生成副本以绕过规范验证。

FAST.Farm 结果须满足[前端实现与数据合同](../../blender_frontend/README.md)和[雷达回放格式](../../wfrl/lidar/REPLAY_FORMAT.md)。按当前 `farm_flex._load_package()` 与 manifest 校验 schema、文件摘要、几何、运动及结果层来源；先验证完整内置包，再接入独立外部输出，不用单个示例字段推定完整格式。

## 风机控制与研究边界

基本风机控制面板、按钮和自动顺桨行为按[手动控制说明](../blender/风机手动控制.md)维护。该栏目不绘制 Down、冻结试验、视频或显示精度控件；研究入口见[手动场景研究接口](../blender/手动场景研究接口.md)。

`live_stream_view.py` 提供现场分屏显示层源码，但 `live_stream.py` 解码层未提供，扩展入口也未注册现场视频。不得把显示层或注册测试文件的存在表述为现场拉流已可运行。

## 验证

测试使用具备所需依赖的既有 Python 环境和独立 Blender 工厂启动进程。宿主检查示例：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=blender_frontend:. python -m pytest blender_frontend/tests -q -p no:cacheprovider
```

后台原生检查使用独立配置和新的输出目录：

```bash
WFRL_TEST_OUTPUT=/absolute/path/new-result blender --background --factory-startup \
    --python-exit-code 1 --python blender_frontend/tests/blender/manual_control_state_regression.py
```

以正常退出、完成标记和结构化结果共同判断通过。后台软件检查、可见窗口、实际输入、完整媒体、性能和科学精度分别报告；文档只保留当前有效行为与已确认要求，完整规则见 [AGENTS.md](../../AGENTS.md)。
