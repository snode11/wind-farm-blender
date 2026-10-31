# X100 数据与采集接口

本包提供独立于 Blender 和训练 Bridge 的 `x100-frame.v1` 合同、严格 PLY 读取器、显式索引记录／回放、几何观测和本地采集通道。依赖 Python 标准库和 NumPy。厂家 UDP 解码、SDK、航迹字段和实机标定尚未接入。

## 帧与来源

`RadarFrame` 的身份是 `(sensor_id, session_id, stream, sequence)`。`source_kind` 为 `GEOMETRY_SIMULATION`、`MEASURED_REPLAY`、`MEASURED_LIVE` 或 `TEST`，文件回放保留记录来源。`NEAR`、`FAR`、`COMBINED`、`TRACK`、`GEOMETRY`、`UNKNOWN` 使用独立序号与新鲜度；`TRACK` 当前仅保留流身份，尚无航迹对象解码与绘制。

点数组固定为不对齐的 `<fffBfffB`，每点 26 字节，最多 4096 点，字段为 `x/y/z/ms/mag/rcs/snr/label`。`ms` 仅支持资料定义的 0／1，原始 label 字节原样保留，不映射成类别或过滤条件。负 RCS／SNR 保留，非有限坐标不参与显示并报告数量；非有限属性不替换成观测零值。几何仿真的 mag、RCS、SNR 用 NaN 表示未知，label 的占位值不具有厂家语义。仿真的 ms 按径向速度绝对值是否大于 `1e-6 m/s` 派生，不代表厂家动静分类算法。

径向速度和材料点身份是独立的可选数组，不放入厂家 PLY 字段。PLY 不含速度、时间戳、帧号和航迹 ID。`FrameTime` 保留时钟域、可缺失的源时刻／采样区间、时间戳参考位置与质量。`ClockMapping` 保存源／目标域、比例／偏移、有效区间、误差及依据；越域、越界或误差超容差时拒绝映射。接收单调时钟只用于当前进程的新鲜度，不写成设备采样时间。

空间字段声明米制、坐标系、运动补偿、对齐状态和可选外参。未确认的实测坐标按 `VENDOR_RAW / UNALIGNED` 展示，不绑定风场动画。内部几何仿真使用右手系 `+X` 前、`+Y` 左、`+Z` 上，外参是同刻 `world_from_sensor`。实测轴映射、原点和运动补偿仍需厂家资料与标定。

## PLY 与显式索引

`ply.read_ply()` 只接受 binary_little_endian 1.0、规定字段类型／顺序和准确的 vertex 载荷长度，拒绝额外 element、缺字段、截断、尾随载荷及超预算文件。合法零顶点 PLY 是有效空帧。没有文件、组帧超时或断流不能生成有效空帧。

按以下 JSON 显式声明序列，不能通过目录文件名排序推断连续性：

```json
{
  "sensor_id": "X100-serial",
  "source_kind": "MEASURED_REPLAY",
  "stream": "UNKNOWN",
  "frames": [
    {"file": "frame_a.ply", "sequence": 0, "replay_time_s": 0.0},
    {"file": "frame_b.ply", "sequence": 1, "replay_time_s": 0.0333333333}
  ]
}
```

`replay_time_s` 是从 0 开始、单调不减的文件呈现时间。已知源时间时，在各项的 `time` 中提供 `FrameTime` 字段；只有用户给出帧率时，可在顶层加 `"fps": 30` 生成 `FPS_DERIVED` 时间，采样区间和硬件同步仍为未知。序号缺口原样保留，不补帧。

```bash
python scripts/radar_x100/import_ply.py /path/to/index.json /path/to/new_recording
```

导入保留同一次读取取得的原始 PLY 字节与摘要。记录包包含 `manifest.json`、`frame_index.jsonl`、`frames/*.ply` 和实际存在时的 `*.velocity.f32`。每项保存完整帧元数据、载荷摘要和回放时间；manifest 保存索引摘要。PLY 逐项只读一次，对同一字节快照校验并解码。`Replay.at(t)` 只选已到达时刻的最后一项原始帧，不插值生成观测；起点前返回无帧。具有相同呈现时间的帧保留独立索引，逐帧操作按索引访问。

`Recorder` 创建 `INCOMPLETE` 标记，只有成功写完并关闭记录才删除。索引预算为 64 MiB，写入与读取使用同一上限；追加超过预算时记录失败，不生成无法被读取器打开的完整包。写盘、校验或记录预算失败进入 `FAILED`；已写文件、最后成功索引行数量和失败原因保留。未正常关闭的包不能作为完整回放打开。

## 几何观测

`simulation.simulate(sample_at, t, ...)` 对固定三角面质心采样，固定随机种子和材料身份，使用名义距离与角域筛选、双面首交几何遮挡。径向速度来自同一材料点与传感器原点在声明差分区间内的相对世界速度，正号表示远离。不建立电磁、穿透、多径、厂家检测概率或噪声分布模型。

Blender 的 `SceneSampler.sample(t)` 只读保存的同源叶片、塔架和机舱运动；不改场景帧、相机或播放器。当前几何为所选展示叶片表面、塔筒与机舱外壳；未包含其他结构、其他机组、环境及合成缺陷的额外拓扑。固定世界／塔基挂载和完整机舱变换使用同一安装标定，变更来源或标定须重新建立采样器。暂停时也核对源绑定和静态根变换，原生修改安装或源根时撤回点云并保留用户修改；着色只更新显示，不重新生成观测。

完整源网格导出使用独立 Blender 进程，默认从源起点导出 1 秒、30 Hz：

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --python-exit-code 1 --python scripts/radar_x100/export_simulation.py -- \
  --output /path/to/new_geometry_recording --candidates 512
```

`--start/--end` 是绝对源时间，`--fps` 是明确的几何采样假设。该脚本加载已有运动包，不运行 FAST.Farm 或训练。

## 外部采集与本地通道

`Assembler` 接收设备适配器已解码的分片，要求一致帧身份、显式分片总数、终止证据和点数预算；只有全部分片到齐才能输出完整帧。已声明损坏的采集单元不能升级为完整帧。适配器负责正式线协议校验、片索引和结束标志解释。未知协议不能直接送入组帧器。超时、重复、损坏及容量溢出分别计数；会话重启调用 `begin_session()` 清除该设备旧组帧身份。

`Fanout` 在提交边界复制点数组、时间和元数据，同时交付有界记录队列与每流最新显示槽；生产者在提交后修改原对象不会改写已接受记录。元数据导出不返回内部字典引用，帧元数据须满足本地通道的容量预算。关闭与提交互斥，关闭开始后拒绝新的记录提交。显示替换有计数，不丢弃记录任务；记录队列溢出或写盘失败停止该记录并保留不完整标记，显示仍可继续。记录完整性只描述实际接收并提交的范围，不承诺恢复设备／UDP 已丢数据。记录 worker 不访问 bpy。

`LocalPublisher` 仅监听 `127.0.0.1`，支持一个显示客户端。每包为 `<4sII` 头（magic `X1F1`、JSON 元数据长度、二进制载荷长度），后接 UTF-8 JSON 和点数组／可选速度数组；元数据最多 256 KiB，数组最多 `4096×30` 字节。HELLO 声明版本、来源、传感器和会话；会话变化必须重连。`LocalClient` 非阻塞且有每次字节／帧数／时长预算，独立判断各流等待、有效、有效空帧、过期与断开。当前不提供远程监听、跨主机认证转发或自动重连。

```bash
python scripts/radar_x100/serve.py --adapter trusted_local_adapter:frames --record /path/to/new_recording
python scripts/radar_x100/serve.py --replay /path/to/recording
```

适配器工厂返回规范 `RadarFrame` 迭代器。`--replay` 仅用于软件通道检查，保留文件来源，不构成真实设备接入。实机适配器需补足匹配固件的 UDP／SDK、模式、单位、轴方向、帧号／时间和空帧合同。
