# 同源资源来源与时钟负向复核

本复核只读正式源码和资产，使用本目录 `fixtures/` 小副本。不修改原资源、生产源码、日常安装或用户场景；没有重跑原 2404 PNG 哈希、纹理、几何或 FAST.Farm。

宿主环境：`blade_recon/.venv-trial/bin/python`。运行时测试调用当前 `split_mappo_texture.ensure`，使用真实 `ReconSequence`、`TexturePackage` 和左侧随包 MAPPO 源文件哈希校验，在第一个材质分配处用哨兵终止。因此 `ACCEPTED_TO_ALLOCATION` 仅表示所有实际运行时导入校验已接受，不声称打开 Blender 或完成网格分配。构建测试调用 `_validate_mappo_texture_payloads`，不写 ZIP。

## 可复现问题

1. **运行时遗漏必需模型身份**：从资源 manifest 删除 `model` 字段，其他文件保持原字节，运行时 `ensure` 仍走到材质分配；构建校验拒绝。`split_mappo_texture.py` 的 `ensure` 文件记录校验没有要求 `model.sha256`；`blade_recon_data.py` 的 `ReconSequence` 仅在 `claimed_model` 为真时核对模型哈希，因此缺失身份被默许。正常非匹配模型哈希会被拒绝。

2. **运行时遗漏逐样本绝对时刻和显示帧映射**：只将 `recon.frames[13].sim_t` 加 1，或只将该行 `blender_frame` 加 1，按正常派生包规则更新该 `recon.json` 的 manifest 哈希和字节数，运行时仍接受；构建拒绝。`split_mappo_texture._validate_clock` 当前检查 601 个 `t`、`frame`、10 Hz 及 40×32 网格，没有核对每行 `sim_t=117+t` 和 `blender_frame=1+6*frame`。这不是绕过数据哈希：副本是哈希自洽但时钟语义错误的包。

3. **构建遗漏声明的绝对时钟**：仅将 manifest `clock.simulation_start_s` 从 117 改为 0，构建校验仍接受；运行时正确拒绝。`build_extension._validate_mappo_texture_payloads` 当前核对相对时刻、样本和 source frame，但没有核对声明的 simulation start/end、timeline fps、stride 或显示 frame start/end。这会允许生成随后无法由正常入口导入的 ZIP。

当前正式包均具有正确模型身份和时钟字段，不受这些负向副本影响。建议共享同一必需合同校验，使导入、嵌入恢复和构建在相同缺失／错误字段上都拒绝。

## 已检查且适当拒绝的运行时案例

- PNG 单字节变化且哈希未更新。
- 显式错误 model SHA-256。
- 保存重建 fps 改为 9 Hz。
- manifest 声明仿真起点错误。

正常资产基线通过运行时和构建校验。完整结果是 [runtime-negatives.json](runtime-negatives.json) 和 [build-negatives.json](build-negatives.json)。小副本和复现脚本 review_negative.py（本地路径：`outputs/mappo-same-source-texture-20261007-a01/self-check/data-review/review_negative.py`） 保留。

复现运行时命令：

```sh
blade_recon/.venv-trial/bin/python outputs/mappo-same-source-texture-20261007-a01/self-check/data-review/review_negative.py
```

本复核尚未执行嵌入文本被改变、形态键/UV人工编辑或 packed 图片替换后的真实 Blender 恢复测试，不把相关静态观察升级为已复现问题。
