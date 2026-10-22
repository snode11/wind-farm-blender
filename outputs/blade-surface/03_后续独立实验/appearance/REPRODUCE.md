# 复现入口

目录为 `REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/appearance`。输出禁止覆盖已有 surface.json；复现时先复制 scripts/frozen_source/input/frames/protocol 到另一个已授权目录并同步 sandbox 白名单/路径，保留本 run 结果。

实际提取复用只读 Python 环境（numpy2.5.3、OpenCV5.0.0）；未安装新依赖或改共享环境。以下命令均在 appearance cwd 执行，Python `-B` 不写只读 runtime pycache：

```sh
sandbox-exec -f appearance.sb 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' -B scripts/build.py A2_new
sandbox-exec -f appearance.sb 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' -B scripts/build.py A3_D12
sandbox-exec -f appearance.sb 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' -B scripts/verify_packages.py
sandbox-exec -f appearance.sb 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' -B scripts/probe_os.py
```

原生 driver 位于 `../validation/display/scripts/validate_window.py`。使用独立 factory profile；所有 source-module viewer 文件为 byte-identical 工作区副本。driver 内存 adapter 仅对缺失旧 schema 的 observed_sections 添加全0 unknown：产品原reader直接加载本run新recon会拒绝；保存重开仍须使用该 driver/adapter。

```sh
cd ../validation/display
sandbox-exec -f ../../appearance/appearance.sb env \
 BLENDER_USER_CONFIG="$PWD/profiles/A2/config" \
 BLENDER_USER_SCRIPTS="$PWD/profiles/A2/scripts" \
 BLENDER_USER_DATAFILES="$PWD/profiles/A2/datafiles" \
 SURFACE_PACKAGE="$PWD/../../appearance/packages/A2_new/surface.json" \
 SURFACE_VALIDATION_OUTPUT="$PWD/A2_window" \
 /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python scripts/validate_window.py
```

A3 使用相同入口，并将 profile、包与输出改为 `A3`、`A3_D12` 和 `A3_window`。比较入口 `scripts/build_comparison.py` 使用 `SURFACE_A2`、`SURFACE_A3` 与 `SURFACE_COMPARISON_OUTPUT`；构造121.2 s 静态样本，两组显示平移 y±82 m，局部mesh不变。`scripts/open_comparison.py` 用同 adapter 独立窗口重开已保存文件。


v2完整取景只执行显示camera/layout，不重做154项；实际入口在display cwd：

```sh
sandbox-exec -f ../../appearance/appearance.sb env \
 BLENDER_USER_CONFIG="$PWD/profiles/full_frame_v2/config" \
 BLENDER_USER_SCRIPTS="$PWD/profiles/full_frame_v2/scripts" \
 BLENDER_USER_DATAFILES="$PWD/profiles/full_frame_v2/datafiles" \
 /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python scripts/full_frame_v2.py
```

driver先UI-ready打开原局部静态比较，完整取景/静态render/save/reopen后留窗口；独立CUA核对title/URL/像素后，在comparison下写 `v2_capture_request.json`，下一实际timer输出 `v2_independent_visible_window.png`、`v2_reopen_checks.json` 与 `v2_read_audit.json`。既有结果不得覆盖；复现使用新授权输出目录并调整explicit paths/sandbox。正式v2各文件及root已查看截图SHA见decision与SHA manifest。
