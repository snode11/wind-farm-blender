# 安装 Reader 兼容性审查

2026-10-07，独立后台 Blender 5.2.1 LTS 审查结论为 **FAIL_UNSUPPORTED**。4个计划帧42/43/45/47全部评分，没有剔除失败或缺失分母。此结论是交付JSON的模型重现不兼容，不是实际动态源几何精度验收。

实际运行安装模块为 `bl_ext.user_default.wfrl_blender`，载入路径 `USER_HOME/Library/Application Support/Blender/5.2/extensions/user_default/wfrl_blender`。实际API名为 `blade_recon_data.ReconSequence`；交接名称 `ReconstructionSequence` 不存在，第一次按该名称调用的异常保留于 `attempt01-requested-api-name.json/log`。最终评分改用真实API `ReconSequence.from_data`，只读取同一重建JSON，防止构造器自动发现邻近truth/camera文件。

| 来源 | SHA256 |
| --- | --- |
| B_final重建JSON | `c2289fdae90d1a329f31d90b4aa0059bd5fdeef46ddff98b50302ac559b9b11e` |
| 冻结外部 `source-candidate/blade_recon0.2/model.py` | `427e6073d21b8c68f8ceaea44f7177ce3a761bbe5faa3fa9c65ad4643b74967c` |
| 实际安装 `blade_recon_data.py` | `aca265887c99cdd55ed724ab687f8e36f157952cb26b3bd5707c7085ff9fbf08` |
| 实际安装 `_vendor/blade_recon/model.py` | `9253fdfb7bb1e76790129511a24b98311c08c7c14a2a54b0c012d260842b888c` |

安装 `_config` 只遍历其内置解析 `TurbineConfig().to_dict()` 字段，静默忽略新增 `healthy_template` 和 `healthy_template_sha256`。它因此保留旧解析截面与径向grid；JSON中健康模板的显式身份未被重现。相同状态向量生成的两份3×40×32网格全部不同。

| 源帧 | 同索引网格差 max | 同半径/环身份差 P95 | 同半径/环身份差 max | 判定 |
| --- | --- | --- | --- | --- |
| 42 | 3.7043m | 1.1404m | 1.6407m | FAIL_UNSUPPORTED |
| 43 | 3.7110m | 1.1403m | 1.6404m | FAIL_UNSUPPORTED |
| 45 | 3.7109m | 1.1403m | 1.6402m | FAIL_UNSUPPORTED |
| 47 | 3.7122m | 1.1403m | 1.6402m | FAIL_UNSUPPORTED |

同索引差包含固定径向grid不同，不能当作相同物理表面点精度。同半径对照将安装网格沿其明示半径线性插值到外部健康模板半径，环身份维持TE→pressure→LE→suction，没有ICP、nearest-source或真值；它仅用于定位模板不兼容，不能当作实际源缺陷点误差。

脚本没有调用 addon.register、review.load、场景生成、渲染、保存或安装。运行于新的 `--background --factory-startup` 进程，未使用任何可见窗口；`sys.dont_write_bytecode=True`，导入安装文件的运行前后SHA一致，记录在 `installed-reader-comparison.json`。生产源码与安装扩展未修改。

复现命令（从实际 Git 根目录）：

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python outputs/blade-recon-v02-stage4-20261007-a01/healthy-contract/installed-compatibility/review_installed_reader.py
```

当前交付应由冻结外部模型生成独立native网格并保存blend，再分别核对重开后的网格与贴图身份。不能将旧安装 reader 的错误模型显示计为健康模板显示通过。安装reader后续应显式识别健康设计合同、验证hash并拒绝不支持模板；本次只审查，不更新安装。
