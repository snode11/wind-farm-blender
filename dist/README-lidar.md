# 独立净空演示数据

[lidar-delivery.json](lidar-delivery.json) 指定 normal/close 外部结果包、来源摘要及对应的 0.3.9 安装附件。该附件身份只属于其自身，当前源码与公开扩展入口见[发布状态](../docs/blender/发布状态与验证范围.md)。

`results/lidar/packages/normal-v1.1/` 与 `close-v1.1/` 是独立刚性姿态演示入口，不与扩展内置的 MAPPO 柔性运动数据混用。它们随源码分支提供，读取器严格校验 manifest 与文件摘要。缺失、损坏或不匹配时应报错，不能借用真值补齐测量。

大型原始输入、评估数据与源码依赖的分发及恢复方式见[项目资源](../docs/PUBLICATION.md)。雷达结果保持 `REVIEW_ONLY / PENDING_ACCEPTANCE`，软件回归不构成现场精度或保护验收。
