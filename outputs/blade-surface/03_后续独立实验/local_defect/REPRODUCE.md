# 局部观测门槛复现

从实际Git根 REPOSITORY_ROOT 执行。源 /outputs/blade-recon-mappo-two-camera-20261004-a02/MAPPO_original.blend；SHA固定在attempt02/capture/result.json。Blender5.2.1，工作区源码；未安装扩展或重跑模拟。

正式命令记录在attempt02/capture/run.log对应驱动scripts/capture_conditions.py。驱动默认写attempt02；复现必须先复制驱动到新run并改输出路径，不能在原目录覆盖。BLENDER_USER_CONFIG使用新run独立config，以 --background --factory-startup --python-exit-code 1 --python 驱动运行。

独立评价采用 blade_recon/.venv-trial/bin/python scripts/evaluate_gate.py （驱动中的BASE/RUN解析为本run）。评价读取private source mesh，必须在新捕获全部结果先SHA冻结后执行；不得放进任何求解/初始化或纹理进程。18张图像使用cv2解码精确比较。当前所有dent图像逐像素等于healthy。

先冻结protocol_frozen.json；attempt02协议只修复输出路径和metadata类型。首次失败的capture/run.log与input/private_evaluation保留，不当正式文件。
