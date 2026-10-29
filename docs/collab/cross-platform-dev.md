# 跨平台协作开发指南

本文档面向新加入的开发者，说明如何在 macOS 上搭建开发环境、基于现有功能继续开发，以及与 Windows 端协作的工作流程。

## 仓库与分支约定

- **远程仓库**：`https://github.com/snode11/wind-farm-blender`
- **主分支**：`main`，稳定功能合并到此处
- **开发分支**：从 `main` checkout，命名建议 `feature/<简短描述>`，例如 `feature/load-display`
- **不直接推送 main**：通过 Pull Request 合并，双方互相 review

```bash
git clone https://github.com/snode11/wind-farm-blender.git
cd wind-farm-blender
git checkout -b feature/your-feature
```

---

## 环境准备（macOS）

### 1. Blender

从 [blender.org](https://www.blender.org/download/) 下载并安装 **Blender 5.2.1 LTS**，与 Windows 端保持版本一致，避免 bpy API 差异。

### 2. FFmpeg

```bash
brew install ffmpeg
```

用于视频导出功能，路径会被 Blender 扩展自动检测。

### 3. 构建扩展

项目使用官方脚本打包，macOS 上直接运行（不需要 `--overwrite` 绕过硬链接问题）：

```bash
python3 scripts/blender/build_extension.py --output dist/my-build
```

构建产物在 `dist/my-build/wfrl_blender-*.zip`，在 Blender → Preferences → Extensions → Install from Disk 安装后**完全重启 Blender**。

### 4. 验证安装

在 Blender Python 控制台确认模块路径：

```python
import sys
print([m.__file__ for n, m in sys.modules.items() if n.endswith('wfrl_blender')])
```

---

## 仓库结构

```
blender_frontend/
  wfrl_blender/           # Blender 扩展主体
    __init__.py           # 注册入口
    farm_flex.py          # FAST.Farm 结果包加载和验证（核心）
    deflection.py         # 叶片挠度计算
    clearance_replay.py   # 净空回放
    manual_control.py     # 手动风机控制
    manual_down.py        # Down 相机与视频导出
    live_stream.py        # 现场视频流（无 bpy 依赖）
    live_stream_view.py   # 现场视频分屏显示
    assets/
      mappo/              # 内置 MAPPO 演示数据包
    panels/               # N 面板各功能页
  tests/
    blender/              # 需要 Blender 运行的回归测试
    test_*.py             # 纯 Python 单元测试（可直接 pytest）
scripts/
  blender/
    build_extension.py    # 扩展打包脚本
docs/
  blender/                # 功能说明文档（中文）
AGENTS.md                 # 开发规则，必读
```

---

## 数据包格式（FAST.Farm 对接关键）

所有 FAST.Farm 结果需要打包成扩展认识的格式才能加载。核心验证逻辑在 `farm_flex.py` 的 `_load_package()` 函数，关键约束：

- `manifest.json` 的 `schema` 字段必须是 `wfrl.farm-flex-review.v1 / v2 / v3` 之一
- `manifest.json` 的 `files` 字段列出每个文件的 SHA-256，不匹配则拒绝加载
- `geometry.npz` 提供三机、每个时刻的变换矩阵和叶轮姿态
- `data.json` 提供每台机的逐帧运动数据（偏航、叶轮角、转速、桨距）

```
assets/mappo/
  manifest.json           # schema、SHA-256 索引、时间段信息
  geometry.npz            # 三机几何变换 (n_times, 3, 3, 19, 3, 4)
  data.json               # 逐帧运动数据
  telemetry.json          # 可选：遥测通道
  deflection-t1.json      # 可选：BeamDyn 挠度映射
  tower-motion.npz        # 可选：塔顶运动
```

**推荐做法**：先用内置 `assets/mappo/` 的数据包跑通加载，再把自己的 FAST.Farm 输出转成同样格式。

---

## 新功能开发方向

### 载荷显示（新功能）

当前项目无载荷数据可视化。开发路径建议：

1. 在 `data.json` 或新增 `loads.json` 里写入 FAST.Farm 的叶根力矩、塔基载荷等
2. 在 `manifest.json` 的 `files` 里加上 SHA-256
3. 在 `farm_flex.py` 的 `_load_package` 读取载荷数据
4. 在 `panels/farm_replay.py` 新增可视化：箭头、颜色映射、曲线图等

### 净空（已有基础）

- `clearance_replay.py` 是主模块
- `panels/clearance.py` 是面板
- 接入新格式时，对照 `ReplayReader` 接口和 `data.json` 里已有的净空字段

### 叶片挠度（已有基础）

- `deflection.py` 处理 BeamDyn 映射
- `deflection-t1.json` 是 T1 的挠度数据格式样例
- `assets/mappo/manifest.json` 中有 `deflection-t1.json` 的 SHA-256 示例可参考

---

## 运行测试

**纯 Python 单元测试**（macOS 直接跑）：

```bash
cd blender_frontend
python -m pytest tests/test_*.py -q
```

**需要 Blender 的回归测试**（后台模式）：

```bash
WFRL_TEST_OUTPUT=/tmp/out_xxx blender --background --factory-startup \
    --python blender_frontend/tests/blender/manual_control_state_regression.py
```

---

## 开发规则（摘要，完整见 AGENTS.md）

- 文档只描述当前有效状态，不记录修改历史和开发过程
- 完成功能后区分"已运行通过"/"仅静态检查"/"尚未验证"三种状态
- 不直接推送 main，用 PR
- 安装版验证须核对实际模块路径（见上方验证步骤）

---

## Windows ↔ macOS 协作说明

| 项目 | Windows 端 | macOS 端 |
|---|---|---|
| Blender 版本 | 5.2.1 LTS | 5.2.1 LTS（保持一致） |
| Python | Blender 内置（用于构建） | Blender 内置（用于构建） |
| FFmpeg | 手动安装，路径在扩展偏好设置中指定 | `brew install ffmpeg` |
| 构建命令 | 需要 `--overwrite`（G 盘不支持硬链接） | 直接运行，无需 `--overwrite` |
| FAST.Farm | Windows 版 | macOS 版（各自独立） |
| 数据格式 | 共用同一 `manifest.json` schema | 共用同一 `manifest.json` schema |

行末换行符：项目使用 `.gitattributes` 控制，clone 后不需要手动配置，macOS 上不会出现 CRLF 问题。
