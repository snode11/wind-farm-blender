# 0.3.6 四相机版注册修复与本机安装

已修复并完成安装。ZIP SHA-256：`92cdeb14e4350c39aef196cd62322c06bf507f35321acde6c15b170d67ce0de9`。安装路径：`/Users/eason/Library/Application Support/Blender/5.2/extensions/user_default/wfrl_blender`。

唯一业务修改是 `panels/custom_cameras.py` 的注册时机：初次场景迁移推迟到注册后的 timer，卸载时取消待执行迁移。原 load_post 与 watchdog 保留。新注册回归、宿主机 11 项、安装版原生事务 14 组、安装版 GPU 窗口 9 组均通过。窗口为脚本驱动，不作为陌生用户测试。

实际从 `bl_ext.user_default.wfrl_blender` 命名空间加载，启动使用原偏好的临时副本；自动启用状态为 True，模块来自本机安装目录，全部 96 文件与新包一致，未向 sys.path 注入源码目录。全局偏好未改。窗口测试确认延迟迁移已执行且 watchdog 已注册。

[验证结果](verification.json)、[注册测试](registration/result.json)、[原生结果](native/result.json)、[窗口结果](window/result.json)、[宿主机日志](host-tests.log)。退出仍有既有 allocator 0.038 MB 提示，无新的注册或回归失败。

交付目录的 ZIP、SHA256、inventory、README 和清单已同步；最新版使用说明与 v3 实施记录追加了安装边界。没有改根 README／CHANGELOG，没有提交、发布、改物理数据或用户 .blend。

清理明细见 cleanup.json：删除本任务产生的失败包与临时安装副本；被替换的旧 0.3.6 安装副本在成功验证后删除。保留既有源码和证据，不删除历史 0.3.3–0.3.5 发布产物。
