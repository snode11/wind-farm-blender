# 独立只读评价指标复核

范围：`evaluation/scripts/evaluate_surface.py`、`evaluation/metric_protocol_development.json`，及脚本调用的 `wfrl/nrel_reconstruction/evaluate.py` 距离/采样实现。没有编辑root脚本、重建数据或产品源码；没有读取源geometry、绑定或WideEval。代码身份见 `reviewed_source_hashes.json`。

结论：当前主表面距离的精确material-region裁剪、面积抽样、双向均权、逐时刻等权及混合P95实现未发现新的实质算法缺陷。下列两项初报问题已由root修正，修后代码已重新只读核对；剩余事项是协议防护/失败交付边界，不能据此声称当前开发分数已受影响。

## 已确认

1. `clip_material_mesh` 对每个三角片以顶点固定material xi的线性插值作两次半空间裁剪，再fan triangulate；不是当前MODEL z截断，没有生成区域边界的人工封帽。边界相交比例和polygon顺序正确；零面积片不影响area抽样与距离表面。
2. 内/中/外三个xi带互不重叠，global由裁剪后片集合并构成；global独立按合并表面面积抽样，避免把面积不同的三带强制等权。source/template各自以其当前三角面积归一化，构成协议所述双向指标。
3. `sample_surface` 按triangle area概率抽取片，再用sqrt barycentric均匀取点；每方向样本数相同，因此串接source→estimate与estimate→source实现.5/.5双向均权。
4. 单clip里，各frame使用同样两方向样本数。frame mean再算术平均为等frame权重；P95取全部方向/时刻样本混合分布的分位数，没有平均方向P95。三clip等权的最终汇总不在此单clip脚本中，需要外部调用方保留预声明3clip分母；本次未审计尚未执行的三盲窗汇总。
5. `point_to_surface_distances` 比较正交投影在三角内的plane距离与三条edge最近点；不是nearest-vertex近似。无ICP/pose/scale再对齐，没有修改估计state/q。
6. source lower arc的mod48选择包含47→0 seam、排除0→1 upper侧；template上半环选择排除16→17和31→0 lower侧。该确认以协议固定source ring48/template ring32布局为前提；没有读取源mesh验证物理侧面映射，源lower→模型upper的物理对应仍依赖root/capture已记录的独立mapping证据。
7. `--selftest` 在禁止整个wfcrl数据读取、仅白名单代码/模板实现/本runtime可读的系统sandbox内实际PASS：精确裁剪面积、area sampling、0.05m平行平面point-to-triangle距离。`selftest_read_audit.json`保留open请求。使用`python -B`，本次沙箱运行不写root字节码缓存。

## 初报问题及修后确认

- 原脚本遍历实际recon帧，planned又从生成行数推导，截短/重复frame或缺WideEval可能改变分母。修后`expected_frames`要求唯一且与recon有序列表完全一致；逐frame校验声明camera恰好各一个；planned按12×声明frame split计算，长窗后续split名称也动态更新。
- 原正深度但出框的finite投影仍进入有效pixel统计。修后只有正深度/在画面预测进入有效统计，failed保留完整planned分母，所有finite raw error另存diagnostic，failed禁止success-only ranking。
- 更正初报中的泛化例：本仓库`Camera.project`本来已把z≤0.05的uv设NaN，因此“behind-camera可以保留小finite uv error”不会在此实现发生。原缺陷主要是正深度出框仍计有效，不能把该例误写成已观察到的bug。
- 已知q_eval进程隔离问题由root单独修正：当前评价仅消费schema/hash/provenance校验后的strict文件，已移除评价进程的intersect初始化。没有重新评价此已知问题或读取源truth验证q数值。

## 剩余防护与适用边界

- **异常交付**：空面积、非finite几何、frame缺失等会显式raise，未悄悄计0或略过；但脚本无`finally`，异常时不会生成成功末尾的read_audit/summary，也没有内置FAILED/null与planned-denominator artifact。调用方必须保存失败decision、已尝试读取审计和完整预声明分母；不得从成功summary列表中省掉失败window/group。建议root在评价driver边界统一完成此失败封装。
- **身份和布局防护**：当前仅检查12个track数和与q_eval的ID列表相等，没有显式检查ID唯一性；source环数48硬编码，template环按config而协议写32。当前固定输入应已受capture/freeze保护，但正式复用时应校验唯一12匿名ID、源/模板layout身份及region/seed/density协议一致，避免未来变更仍沿用旧指标名称。
- **相机可见性语义**：当前`prediction_in_frame`是正深度与图像边界，不等于背面、遮挡或RGB材料点可见。secondary projection表应保留这一名称与意义，不宣称其已经验证材料可见/遮挡；任何需要front-facing/occlusion的门槛应使用单独冻结定义及评价证据。主区域surface distance不依赖此visibility proxy。
- 本次selftest没有验证保存源输入的MODEL坐标转换、source-side mapping、采样密度稳定性、最终三盲窗统计或现场精度，这些需要各自实际证据。

## 复核复现

从tracking目录执行：

```sh
sandbox-exec -f metric_review/review_selftest.sb ../runtime_tracking/venv/bin/python -B ../evaluation/scripts/evaluate_surface.py --selftest
```

`review_selftest.sb`仅许可指标/模板实现代码和本runtime读取，禁止源private数据及网络，并禁止root文件写入。review是只读代码与toy selftest，维持`REVIEW_ONLY`，不改变任何P2b评价结果。

## 复核快照之后的 root 更新

root 后续告知已增加异常 FAILED/null + read_audit_failed、12ID唯一性与协议seed/density检查，并在新surface评分前完成；核心区域/采样/距离/均权公式不变。这是root后续修复记录，本文件前述源码SHA仍代表实际只读复核快照，不冒称该快照已经包含后来guard。相关原生外观兼容问题由本run独立driver处理，未改metric/source geometry。
