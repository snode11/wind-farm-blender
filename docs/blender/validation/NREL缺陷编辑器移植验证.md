# NREL 缺陷编辑器移植验证

本页“本地归档”仅用于标识原验证或开发材料，未随本次文档同步公开；安装请使用对应 Release ZIP。

2026-10-05，本地 macOS，Blender 5.2.1 LTS。移植对象为主前端的 NREL 5MW / MAPPO 保存结果柔性回放，覆盖 T1、T2、T3 共九片叶片。GW184 独立项目未改写。该开发工作没有运行 FAST.Farm / BeamDyn、修改已有物理结果或执行图片渲染。移植完成后，按用户要求将功能随 0.3.16 ZIP 公开发布，仅上传 ZIP 附件，未提交或推送源码；发布包验证见 [0.3.16 发布核对](../releases/0.3.16发布核对.md)。

## 功能与结果

| 检查 | 结果与实际范围 |
| --- | --- |
| 宿主机 | 12 项通过：六形态、两侧参考定位、三机配置、事务失败回滚、撤销重做、严格参考身份、重叠判断、投影面积并集和绝对时间映射。 |
| 源码后台 | attempt07（本地归档：`outputs/blender-validation/nrel-defects-20261005/attempt07`，未公开） 的保存与新进程重开均通过。检查六形态、九片叶片、预览取消/确认、UI 编辑操作、健康对照、启停、JSON 加载失败保持原状态、任意时刻/末帧/反向寻址、支持区域与辅助轮廓跟随、末尾两类叶尖索引、源数据摘要与雷达读取器不变；寻址期间 Boolean 调用为零。 |
| 开发 ZIP | package04 验证（本地归档：`outputs/blender-validation/nrel-defects-20261005/package04/package-validation.json`，未公开） 通过 ZIP 清单与隔离安装字节检查、扩展命名空间确认、默认三相机与原生投影回归。145 个文件。 |
| 安装目录中的编辑器 | save-checks.json（本地归档：`outputs/blender-validation/nrel-defects-20261005/package04/regressions/nrel-defects/save-checks.json`，未公开） 与 reopen-checks.json（本地归档：`outputs/blender-validation/nrel-defects-20261005/package04/regressions/nrel-defects/reopen-checks.json`，未公开） 均通过，模块实际来自 `package04/profile/extensions/wfrl_blender/`。 |
| A 失败恢复 | 独立几何检查（本地归档：`outputs/blender-validation/nrel-defects-20261005/a-geometry-probe/geometry-checks.json`，未公开） 通过：主动抛错后九片叶片的网格身份、全部坐标、材质槽与调用前完全相同，场景设置和时刻恢复，没有调用渲染器。 |
| G 命令行扫描 | g-scan03（本地归档：`outputs/blender-validation/nrel-defects-20261005/g-scan03/run_manifest.json`，未公开） 完成，六形态、三个 1920×1080 相机、117.00/117.05 s 两个离散时刻，共 72 条记录；无 INVALID，36 OUT_OF_FRAME、24 PARTIAL、12 VISIBLE。区间文件已生成，图片产品未生成。 |
| 源码实际窗口 | 首个独立窗口中直接观察到 NREL 面板、六形态列表、开口裂缝、凹坑与侵蚀显示；这只证明对应视口内容出现。window02 操作检查（本地归档：`outputs/blender-validation/nrel-defects-20261005/window02/result/window-checks.json`，未公开） 的编辑/预览/取消、逐帧扫描与失败恢复均通过；检查完成后的控制台切换触发原生崩溃，整体窗口稳定性不计为通过，见下文。 |

窄小支持三角形的解析坐标保留双精度，避免单精度在约 50 m 的局部坐标处使材质区域退化；显示网格与辅助对象保持 Blender 原有单精度坐标路径。相机分析采用各相机实际输出尺寸，并冻结局部安装/镜头配置；跟随机舱的世界位姿不当作安装被修改。

## 使用入口与发布包

双击打开NREL缺陷编辑器.command（本地开发归档：`scripts/blender/打开NREL缺陷编辑器.command`），然后进入 MAPPO 侧栏的「NREL 5MW · 叶片缺陷」。完整操作见[NREL 缺陷编辑器](../NREL缺陷编辑器.md)。

正式发布包：[wfrl_blender-0.3.16.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.16/wfrl_blender-0.3.16.zip)，已发布至 [GitHub Latest](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.16)。SHA-256：`d7a95e3942e60ccc95d5c97052f478eeafab6df0f2a7f4daa7a2fd5a8fec1ba2`。145 个文件中，除 manifest 版本号外的 144 个文件与移植最终开发包一致，见包内容核对（本地归档：`outputs/blender-validation/nrel-defects-release-0.3.16-20261005/package-identity.json`，未公开）；新包隔离安装的保存与重开检查（本地归档：`outputs/blender-validation/nrel-defects-release-0.3.16-20261005/release-editor-validation.json`，未公开）均通过。本次未更新本机日常安装。

移植阶段最终开发包为 `package04` 中的 0.3.15 ZIP，历史 SHA-256 为 `035255e16af50ec66bd9f76091e9e8eb56eaab26b1fff00905dc6102ee6ca20b`；它与原公开 0.3.15 附件不同，现已被正式 0.3.16 包替代。保留的历史 JSON 和日志维持原运行路径，不把历史路径解释为当前仍存在的安装环境。

## 复现命令

以下命令适用于包含该功能的本地规范源码，公开 0.3.13 实现源码不含 NREL 缺陷编辑器；在匹配的本地源码根目录运行，证据目录必须选新的空目录。

```sh
PYTHONPATH=blender_frontend:. python3 -m unittest discover -s blender_frontend/tests -p 'test_nrel_defect*.py' -v

WFRL_TEST_OUTPUT="$PWD/outputs/blender-validation/nrel-defects-check" \
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 \
--python blender_frontend/tests/blender/nrel_defect_editor_regression.py

WFRL_TEST_OUTPUT="$PWD/outputs/blender-validation/nrel-defects-check" WFRL_NREL_DEFECT_PHASE=reopen \
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 \
--python blender_frontend/tests/blender/nrel_defect_editor_regression.py

python3 scripts/blender/validate_frontend_package.py --output outputs/blender-validation/nrel-defects-package --timeout 240

/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 \
--python scripts/blender/nrel_defects.py -- scan --all-presets --time 117 --duration .1 --fps 20 \
--output outputs/blender-validation/nrel-defects-scan
```

本次隔离安装目录和保存重开测试夹具已清理；留存的 `environment.json` 仅记录历史环境。重新验证时，先在新的空目录运行 `validate_frontend_package.py` 生成隔离安装，再沿用新目录的 `environment.json`，设置 `WFRL_ADDON_MODULE=bl_ext.wfrl_frontend.wfrl_blender` 并先运行保存阶段生成 `.blend`、`expected.json` 后运行重开阶段；不能通过导入裸源码包代替安装证据。

## 已知边界

既有 `tip_deflection_regression.py` 的 0.2 mm 阈值检查仍失败，最大方向误差约 0.895 mm。移除本次新方法与钩子的内存基线得到完全相同的三方向误差，详见基线诊断（本地归档：`outputs/blender-validation/nrel-defects-20261005/tip-baseline-diagnostic.json`，未公开）。本次未修改旧回归阈值或保存结果，该问题不计为通过。

`window02` 完成功能检查后，在 CUA 切换 Python 控制台时发生 SIGSEGV；崩溃栈（本地归档：`outputs/blender-validation/nrel-defects-20261005/window02/result/blender-console.crash.txt`，未公开） 位于 Blender 原生 `console_textview_main__internal / console_char_pick / console_modal_select_apply`，没有 Python 回溯。无插件空场景的两次对应切换/点击未复现，尚不能确定原因或宣称已修复。已保留进程失败状态；该记录不用于整体窗口稳定性验收，也不修改编辑器来掩盖它。

G 的 VISIBLE/PARTIAL 是理想投影与遮挡状态，不证明图片可辨识。实际窗口外观观察也不构成逐形态 A 图验收、深度实测、结构损伤、实体相机同步或性能证据。渲染失败恢复测试使用局部替代的渲染调用主动抛错，没有调用实际渲染器。移植最终实现对应历史 `package04`，正式发布包为 0.3.16；这些已知边界没有因发布而计为通过。

## 中间产物清理

本次按引用移植聊天和新增发布任务的范围，将早期 `attempt01`–`attempt06`、旧扫描、`package01`–`package03`、被替代的 `package04` 开发 ZIP、两次最终验证的隔离安装目录、保存重开 `.blend` 与测试断言夹具、临时脚本及本次代码缓存移入 macOS 废纸篓。保留正式 0.3.16 ZIP、校验和、清单、最终 JSON/日志、扫描记录、配置、叶尖基线诊断与控制台崩溃栈。源码、启动器、内置 MAPPO 数据和便携分屏示例保留。

精确路径、字节数、SHA-256 与恢复映射见清理记录（本地归档：`../清理记录/nrel-defects-20261005/清理记录.json`，未公开）；废纸篓未清空。清理前文档副本随清理记录保留，历史运行记录不改写。
