# 独立净空演示数据

[lidar-delivery.json](lidar-delivery.json) 使用 `wfrl.lidar-delivery.v1` 合同，指定 normal/close 外部结果包、两包 manifest 摘要及对应的 0.3.9 安装附件。清单是正式运行输入，应与两包一同保留，不作为缓存或构建副本清理；单包 manifest 不替代此输入。附件身份只属于其自身，当前源码与公开扩展入口见[发布状态](../docs/blender/发布状态与验证范围.md)。

`results/lidar/packages/normal-v1.1/` 与 `close-v1.1/` 是独立刚性姿态演示入口，不与扩展内置的 MAPPO 柔性运动数据混用。它们随源码分支提供，读取器严格校验 manifest 与文件摘要。缺失、损坏或不匹配时应报错，不能借用真值补齐测量。

大型原始输入、评估数据与源码依赖的分发及恢复方式见[项目资源](../docs/PUBLICATION.md)。雷达结果保持 `REVIEW_ONLY / PENDING_ACCEPTANCE`，软件回归不构成现场精度或保护验收。

在 macOS 上双击[打开净空雷达演示.command](../scripts/blender/打开净空雷达演示.command)，或从仓库根目录执行：

```sh
/Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python scripts/blender/open_clearance_demo.py
```

入口使用项目源码与独立 Blender 进程，不安装扩展、不启动 Bridge 或 FAST.Farm。侧栏 **WFRL / Camera 相机与演示 → 净空与误差对比** 提供“正常测量”和“较小净空”两个片段；用法见[用户手册](../docs/blender/用户使用手册.md#12-独立激光净空雷达正常较小净空片段)。
