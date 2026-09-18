WFRL Blender 0.3.4

下载 wfrl_blender-0.3.4.zip，在 Blender 5.2+ 从磁盘安装并重启；不要选择 Source code。Windows/macOS 共用 ZIP，离线回放无需后端 Python、MPI 或 FAST.Farm。

本次更新：
- 默认内置 BeamDyn 预弯 v3 三机 60 秒结果，独立未受载参考与根系 xyz 挠度对照。
- 叶尖轨迹按仿真时间渐隐，保留 1.5 秒；暂停和视角切换保持一致。
- 修复净空页 Down 视角；同步源码默认资源、安装包和更新记录。

验证：19 项宿主测试通过；独立 ZIP 的 macOS Blender 5.2.1 原生回放、视角/FPS 切换及保存重开通过。107 个文件与本机已安装版本逐字节一致。

已知限制：物理数据为 REVIEW_ONLY，固定三束测量覆盖率 FAILED_AVAILABILITY，二维扫描候选尚未接入。Windows 实机、稳定 60 FPS、长期稳定性与现场精度未验收。

SHA-256: 3f2c718e3ae438a49786df4de2f92a1153fd405ea026521d8c19f3e22434a9b0
