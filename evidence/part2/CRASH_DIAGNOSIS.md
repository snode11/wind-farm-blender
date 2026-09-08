# Blender 启动崩溃排查 · 2026-09-06

用户提供的报告：Blender 63647，父进程 codex 62151；10:13:25.488 启动，10:13:25.849 崩溃（约 0.36 秒）；SIGSEGV。报告中的 IlmThread 工作线程堆栈不足以确认具体底层故障函数。

本轮可复现环境差异：Codex 沙盒内启动 Blender 5.2.1 时输出 `ARCH_CACHE_LINE_SIZE != Arch_ObtainCacheLineSize()` 后崩溃，尚未进入 Python 测试。沙盒外相同 Blender、factory startup 能完成 Python 脚本和相机测试，进程正常退出 0。因此启动环境限制是当前最强解释；不能单凭这份报告断言是模型、内存不足或 GPU 驱动故障。

后续处理：本任务所有 Blender 测试和渲染统一使用已获准的沙盒外执行，并串行运行。停止在沙盒内重试，避免重复生成系统崩溃弹窗。不更改用户的 Blender 配置，不重装软件。

第二份报告确认：Blender 63912，10:15:10.868 启动、10:15:11.226 崩溃。主线程 `_platform_strstr → supports_barycentric_whitelist → MTLBackend::metal_is_supported → GPU_backend_type_selection_detect → WM_init`，访问地址 0。对应停止通知到达前子任务最后一次沙盒启动。10:15:52 检查无更晚崩溃。正常权限下纯启动输出 `WFRL_STARTUP_OK 5.2.1 LTS` 并退出 0。

## 第三份报告的对比结果

用户在 2026-09-06 再次提供的报告对应 Blender PID `20712`，启动时间
`17:40:08.2276`，捕获时间 `17:40:08.5832`，约 `0.36 s` 后退出；父进程仍为
`codex`（PID `19040`），异常仍是 `EXC_CRASH (SIGSEGV)`，faulting thread
仍为 `2`。它与前两份的关键字段如下：

| 报告 | Blender PID | 启动时间 | 父进程 | faulting thread | 关键 GPU 镜像 |
|---|---:|---|---|---:|---|
| 第一次 | 20220 | 17:30:32.3917 | codex 19040 | 2 | `AGXMetalG17X 353.14` |
| 第二次 | 20571 | 17:35:04.2734 | Exited process 20538 | 2 | `AGXMetalG17X 353.14` |
| 第三次 | 20712 | 17:40:08.2276 | codex 19040 | 2 | `AGXMetalG17X 353.14` |

三份系统报告的线程 2 都停在 OpenEXR `IlmThread` 的 semaphore 等待；这只是
报告选中的线程，不是崩溃根因。Blender 自己写出的内部回溯在三次中都指向：

```text
_platform_strstr
supports_barycentric_whitelist
MTLBackend::metal_is_supported
GPU_backend_type_selection_detect
wm_homefile_read_ex
WM_init
main
```

没有 Python backtrace，也没有打开工程文件的记录。因此“尾流数量、地形贴图、
Part 5 Python 脚本或 `.blend` 损坏”不能解释这三次退出；它们尚未执行。

当前安装还存在一个独立的可维护性警告：`codesign --verify --deep --strict`
报告 Blender bundle 中新增了 `python/lib/python3.13/__pycache__/code*.pyc`。项目
入口已设置 `PYTHONDONTWRITEBYTECODE=1`，但不会擅自删除或改写
`/Applications/Blender.app`；是否重新安装 Blender 由用户决定。这一警告不能被
当作本次 SIGSEGV 的已证实根因。

## 项目侧保护与边界

`Open WFRL.command` 和 `scripts/blender/verify_part2.py` 现在会在
`CODEX_SANDBOX=seatbelt` 时先做 preflight 并退出，不创建 Blender 子进程。这个
改动修复的是“项目入口反复触发已知崩溃”的问题，不是修复 Blender 或 Apple
Metal。要验证 Blender 本身和完成 Part 5 动画，仍需在 Mac 解锁后从 Finder 或
普通 Terminal（沙盒外）启动，并保留失败报告作为对照证据。
