# 0.3.6 v3 安装尝试：发现扩展注册阻断，已恢复旧安装

当前状态：原安装已恢复且在独立 Blender 进程启动验证 enabled=True、loaded=True。旧 ZIP、源码、全局偏好均未改；没有删除旧版。新版只在临时构建目录保留，未覆盖 dist 正式入口。

## 已证实问题

`blender_frontend/wfrl_blender/panels/custom_cameras.py:982` 在 `register_properties()` 中同步调用 `migrate_loaded()`；后者第 974 行遍历 `bpy.data.scenes`。Blender 通过扩展机制加载插件时使用 RestrictBlend 上下文，此时 bpy.data 为 `_RestrictData`，不能读取 scenes。

触发：将当前源码打包安装为 bl_ext.user_default.wfrl_blender，保留原已启用偏好，在独立 Blender 5.2.1 LTS 进程启动；使用 Blender 原生 addon_utils.enable 再次启用也重复出现。

预期：扩展正常注册，四相机面板和采集设置可用。
实际：`AttributeError: '_RestrictData' object has no attribute 'scenes'`，注册中断、loaded=False，后续 Scene 属性未注册，MAPPO 加载也报错。这是安装版阻断；此前直接调用 addon.register() 的源码试用证据不能覆盖它。

证据：[native.log](native.log)、[window.log](window.log)、[installation-load.json](installation-load.json)。打包合同 2 项通过，但不代表扩展能注册。

## 最小修复建议（未应用）

只修改相机面板注册时机：将注册函数中的同步迁移调用换为一次性 Blender timer，注册结束后访问场景；卸载时取消尚未执行的迁移 timer。保留原 load_post 迁移和 watchdog 行为。拟议变更见 [proposed-fix-not-applied.patch](proposed-fix-not-applied.patch)。不改物理数据、相机操作语义或加入新功能。

得到源码修复授权后应重新构建 ZIP，在实际扩展命名空间验证启用、14 组原生测试、9 组 GPU 窗口测试及新进程重启加载，再替换 dist ZIP 和清理旧副本。当前补丁只是审阅建议，尚未应用或测试。

## 恢复与边界

安装替换失败后已将原目录完整移回，并逐文件核对原有 SHA-256；全局配置文件哈希未变。[rollback.json](rollback.json)、[restored-load.json](restored-load.json)、[restored-load.log](restored-load.log)。测试只使用复制的偏好和临时配置目录，用户 .blend 未打开或修改。原本机源码审查结论只针对源码入口，本次发现补充了安装版的不同边界。
