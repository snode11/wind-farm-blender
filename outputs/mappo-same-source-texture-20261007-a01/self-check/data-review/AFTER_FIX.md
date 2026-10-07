# 修复后的同源来源与时钟负向复核

修复前的 [runtime-negatives.json](runtime-negatives.json) 和 [build-negatives.json](build-negatives.json) 原样保留，after 脚本在结束时重新核对其 SHA-256 未变。原复核报告 [README.md](README.md) 是修复前发现记录。

本次使用相同 8 个小副本案例复核冻结后的源码，没有修改生产代码、正式资产或安装，没有生成 ZIP、纹理、几何或 FAST.Farm 结果。

| 案例 | 运行时修复前 → 修复后 | 构建校验修复前 → 修复后 |
| --- | --- | --- |
| 正常包基线 | 接受 → 接受 | 接受 → 接受 |
| 缺模型身份 | 接受 → **拒绝** | 拒绝 → 拒绝 |
| 错误逐样本 `sim_t` | 接受 → **拒绝** | 拒绝 → 拒绝 |
| 错误逐样本 `blender_frame` | 接受 → **拒绝** | 拒绝 → 拒绝 |
| 错误声明 `simulation_start_s` | 拒绝 → 拒绝 | 接受 → **拒绝** |
| PNG 单字节损坏 | 拒绝 → 拒绝 | 拒绝 → 拒绝 |
| 错误模型哈希 | 拒绝 → 拒绝 | 拒绝 → 拒绝 |
| 重建频率改为 9 Hz | 拒绝 → 拒绝 | 拒绝 → 拒绝 |

结论：三个原运行时漏检和一个原构建漏检均改为拒绝；正常包仍通过。所有 7 个错误案例现在在运行时与构建校验中均拒绝。

运行时的“接受”具体指真实 `ensure` 完成来源、模型、时钟、文件哈希与 `ReconSequence`／`TexturePackage` 校验，到达第一个材质分配位置后由测试哨兵终止。此证据不表示打开 Blender 窗口或实际分配网格；原生及窗口验证由本次总验收单独提供。

结果文件：

- [runtime-negatives-after-fix.json](runtime-negatives-after-fix.json)
- [build-negatives-after-fix.json](build-negatives-after-fix.json)
- [before-after-summary.json](before-after-summary.json)：包括 before 文件保全哈希与实际检查的源码／资产摘要。
- [after-fix.log](after-fix.log)

实际检查源码摘要：

```text
build_extension.py:
03a7f0bc509017f8aadc4908a68ae43aea2548ce87df132dcdf38bf3307d8e55
split_mappo_texture.py:
bd497ff9c81085996869bc2bc8d18a32152c98d3c5293fcbe1ff2076a63ac8e6
同源资产 manifest（保持）：
6e542f6bddc9aa57dd8643a6463e8e20054cbe4a5abdec23b319e1e034718ffb
```

复现命令（保存 after 独立文件，不覆盖 before）：

```sh
blade_recon/.venv-trial/bin/python outputs/mappo-same-source-texture-20261007-a01/self-check/data-review/review_after.py
```
