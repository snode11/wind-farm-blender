# 局部缺陷受控观测门槛实际执行报告

最终状态 **FAILED_GATE / OBSERVATION_GATE_FAILED / REVIEW_ONLY**。健康、纯颜色补漆、真实凹坑三条件已实际捕获并完成未用时刻/视角检查；没有从RGB恢复凹坑深度。

沿用保存MAPPO回放、WideInput/WideEval已知相机、4K、Workbench FLAT MATERIAL/Standard/no shadows/cavity/specular。源副本每时刻先提交保存运动，再在B1局部下侧表面施加紧支持的法向向内位移。源真值只在捕获/独立评价权限读取。原场景、回放、健康模板和安装不改变。本子实验独立，不混入P2b。

开发时刻144.0s；预声明留出144.4/146.0s，WideEval全程留出。3条件×3时刻×2相机=18张正式RGB。第一次18张捕获后metadata float32 JSON写入失败，原图/日志原位保留；仅修复标量序列化，在attempt02新目录重新捕获18张。正式capture elapsed 8.24s，全部请求时刻与提交一致。冻结全部产物SHA后才进行比较。

真实凹坑修改23个顶点，最大实际向内位移约50mm，源支持半径展向3m/弦向0.55m。纯颜色条件源顶点逐值等于健康。两相机全部三个时刻的健康与凹坑解码RGB完全相同（changed pixels=0、所有通道差=0）；PNG字节SHA不同来自文件metadata，不能把字节差当图像信号。纯颜色条件改变2715–3966像素，最大通道差77。存在明显外观信号，却没有当前凹坑的图像识别信号。

独立评价以改变顶点相邻的同侧三角面定义局部支持，复用wfrl/nrel_reconstruction/evaluate.py的面积采样和精确点到三角面距离。以下是**两个图像等价源表面的几何分离**，不是重建误差；无ICP或姿态/尺度对齐，MODEL米制，每方向1024等面积采样、两方向等权、P95取混合分布。512/1024密度对照及逐点数组均保存。

| 时刻s | 双向mean mm | 混合P95 mm |
|---|---:|---:|
| 144.0 | 10.656 | 37.409 |
| 144.4 | 10.656 | 37.409 |
| 146.0 | 10.655 | 37.408 |

固定健康模板的原状态没有局部深度参数，不能表达此凹坑。新增局部表达也无法从这些完全相同的RGB唯一选择健康/50mm凹坑；因此没有为得到数值而继续局部拟合。finite_local_shape_fit=NOT_RUN_OBSERVATION_GATE_FAILED；recovered_depth和纯颜色伪深度均为null，含义是未建立可用估计器，不是测得零深度。

这项负结果仅针对当前平坦材质、内部紧支持凹坑、已知宽相机和受控回放。不能推广为所有光照/纹理/更近视差条件都不可恢复，不能称裂纹深度、内伤或现场诊断。实际输入不足已给出停止证据，后续改变光照/图案/相机需独立版本，不能混入本对照。

证据入口：attempt02/capture/result.json、commit_evidence.json、all_results_frozen_before_comparison.json；attempt02/private_evaluation/image_comparison.json、truth_manifest.json、counterexample_metrics.json、counterexample_distance_arrays.npz和read_audit.json；decision.json。comparison_rgb.png仅为输入RGB三条件对比图。
