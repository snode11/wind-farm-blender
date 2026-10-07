# 任务场景误保存后的恢复

状态为 `PASS_STATE_RESTORED_BYTE_IDENTITY_NOT_RESTORED`。本次只修复任务输出中的 `daily-window/attempt02/same-source-saved.blend`，没有修改规范示例、源码、偏好或旧验收报告。

主代理关闭可见测试窗口时，UI操作意外选择了保存，原已验收文件被保存为 frame55。原字节参考 SHA 为 `abcf9a4951bc406c91577791154a85ff049483c89e271f705e4d8db8ea85995c`；意外保存后为 `a15811cffa7a70acb44c092a4f0b9fe16ba70cf94325815d273e135418149ed4`。已先将该文件完整复制至 `recovery/same-source-saved-at55.blend`，复制 SHA 验证一致。

使用已通过发布验证的隔离 profile，在新 background Blender 5.2.1 进程中载入该任务场景。实际 addon 为 `bl_ext.wfrl_frontend.wfrl_blender`，模块根 `/Users/eason/Desktop/wfcrl/wind farm RL/outputs/release-0.3.17.1-20261008/validation/profile/extensions/wfrl_blender`，manifest `0.3.17+1`。确认 frame55 后恢复到 frame52，并仅保存到原任务输出路径。save_version=0 只在该进程内临时设置，没有保存任何 preference；避免新建自动备份文件。

修复后的当前 SHA 为 `93a4c5ca78f1578f8c884efb9736f69a3ab892ef435fe155961b9c22c6576218`。这不等于旧参考 SHA：frame/数据状态已经恢复，原文件逐字节身份没有恢复，也不宣称已经恢复。旧报告和旧哈希保持原文；请使用 `recovery-summary.json` 与本次新验收报告理解保留场景的当前状态。

恢复前后的全部9项显示属性及实际shader输入保持相同：ORIGINAL、gain6、B2、r约47.225、tau约0.821615、size2、显示选框。原始601 keys、新同源601 keys、模型float32坐标、UV、packed图像和运行数据块身份均严格保持。

随后又启动独立新进程，原封运行 `mappo_same_source_texture_reopen.py`，仍使用原日常窗口PASS报告作为几何/纹理/时钟基线。结果PASS：阻断外部路径/构造器并清空runtime后从embedded/packed原位恢复；全部601 key/model几何bit-identical、UV/打包图片身份、全部数据块及8个帧同步检查通过。这个PASS证明状态/数据恢复，不证明原文件字节相同，也不升级重建精度状态。

`restore-preservation.json` 与 `native-reopen/reopen-preservation.json` 证明 source/daily/isolated规范示例、isolated/daily prefs和旧window报告前后SHA完全相同。重新验收期间任务文件本身也保持修复后的SHA不变。所有新后台进程已退出，没有启动或操作新的可见窗口，没有网络或ZIP比较。

命令、隔离环境、进程记录、日志与报告均在本目录。恢复副本只供清理时保全/回溯，主流程另行决定是否移入Trash。
