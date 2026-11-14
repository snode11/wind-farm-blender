> 迁移后的历史阅读版：链接已更新；原轮次结论与命令按历史理解。[原始字节](../provenance/original-documents/reports/a02-reproduce.md) · 当前启动入口（本地路径：`outputs/nrel-video-dynamic/runs/20261004-integrated/打开研究查看器.command`）。

# A02 复查与复现

本目录保留 A02 的事前协议、输入绑定、启动封套、拒读探针、逐候选结果与源码快照。运行依赖本机 `PYTHON_ENVIRONMENT/bin/python`；[库版本](../validation/refined/library_versions.json) 单独记录。源码快照是证据归档，不是自包含可移植运行包。

1. 核对 [启动封套](../diagnostics/refined/launch_freeze.json) 与 `.sha256`，再核对封套中绑定的协议、输入绑定、策略、探针、环境、命令和启动器哈希。
2. [输入绑定](../diagnostics/refined/input_binding.json) 枚举实际 MP4、相机/时间/根部刚体输入、原 RGB 分割缓存、解码 PNG、静态 v2、理想平面和 Python 源码。不得换成评分/源柔性文件。
3. [实际命令](../diagnostics/refined/command.json)、启动器（本地路径：`outputs/nrel-video-dynamic/runs/20261004-integrated/diagnostics/refined/launch.py`） 与 [环境](../diagnostics/refined/environment.json) 记录执行条件。[隔离探针](../diagnostics/refined/isolation_probe.json) 必须拒读；源数据访问与网络未获许可时不得绕过策略。
4. 需要重跑时在新的运行目录生成新协议/路径绑定/启动封套/输出路径和隔离策略，先核验拒读探针再执行。原 `launch.py` 和输出采用独占创建，直接重跑会拒绝覆盖历史证据。移动输入会改变绝对路径绑定，不能只改命令参数。
5. 结构审计的 [实际命令](../inputs/static-mapping/static-mapping/command.json) 和 [输入哈希](../inputs/static-mapping/static-mapping/input_freeze.json) 单独记录。它只核查静态定义，在宿主机上执行；其源材料偏置和报告未供图像诊断进程读取。

细网格计算复用已有轮廓后端，非时间优化；相机子集只从同一组图像项重新聚合，没有额外评估。无图像全网格范围不是 DY2 的 G2 对照。独立真值/动态评分仍未读取。
