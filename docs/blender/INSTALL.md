# WFRL Blender 0.3.9 安装

要求 **Blender 5.2+**。Windows、macOS 和 Linux 使用同一扩展 ZIP；本次实际验证环境及范围见 [0.3.9 发布核对](0.3.9发布核对.md)。

## 安装与升级

1. 下载 [wfrl_blender-0.3.9.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.9/wfrl_blender-0.3.9.zip)，保留压缩格式。发布页自动生成的 **Source code** 是 `v0.3.9` 标签源码，不是可直接安装的 Blender 扩展包。
2. 保存现有 Blender 工作。进入 **Edit → Preferences → Get Extensions → Install from Disk**，选择 ZIP 并启用 **WFRL Blender**。
3. 升级旧版后退出并重启 Blender，让新的 Python 模块和打包资源生效；仅更新仓库或下载 ZIP 不会替换当前进程中的扩展。
4. 新建 General 场景，鼠标置于三维视图，按 **N → MAPPO → 加载 MAPPO · 60 秒**。不要将旧 `.blend` 快照直接视为新版默认场景。
5. 主视图先显示 T1 总览；进入 **View → 三相机 → 三路对照** 检查 C1/C2/C3 推荐取景。加载后先暂停，播放时观察叶片经过。

发布文件的 SHA-256 及交付验证见[发布核对](0.3.9发布核对.md)。核对时使用下载后的实际文件；版本名相同不等于文件内容相同。

## 离线使用与三相机

扩展 ZIP 内置预弯 v3 三机 MAPPO 60 秒回放数据、读取器和默认三相机配置。安装完成后，离线观看无需克隆仓库、配置外部 Python、MPI 或 FAST.Farm，也不运行新的求解或训练。

加载 MAPPO 会创建共盒三相机及外伸支架，并应用连续重叠的默认取景，无需另行导入 JSON。C1/C2/C3 分别以叶根、中段和叶尖为重点；固定光心随机舱运动，不持续追踪 B1，不按叶长范围裁剪画面。根部、极尖端及部分表面仍可能被机舱、连接件或叶片自身遮挡。

安装调整、单路／三路观察、流畅／高清切换、布局导入导出和同刻／序列 PNG 采集见[详细前端说明](../../前端readme.md)及[三相机说明](T1三相机使用说明.md)。旧四路 JSON 不适用于当前三相机配置。

三路播放仍可能卡顿，并有待排查的切换后停止异常；当前不承诺稳定 30／60 FPS。物理数据保持 REVIEW_ONLY，固定三束测量覆盖率未通过，画面不代表实体相机标定、拼接成功或现场雷达精度验收。

## 源码与后端

对应源码由 [v0.3.9 发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.9)和标签提供。从仓库根目录构建：

```bash
python3 scripts/blender/build_extension.py
```

构建后安装生成的扩展 ZIP 并重启 Blender。修改源码不会自动更新已安装扩展；具体构建资源、验证入口及后端环境见[实现与维护](../../blender_frontend/README.md)。真实后端运行需另行配置项目环境，不能用离线安装成功代替后端验收。

MAPPO → 视频输出提供现有 MP4 导出与 RTSP 循环推流；推流需配置 FFmpeg 和 MediaMTX，详见[视频输出说明](Blender视频输出.md)。
