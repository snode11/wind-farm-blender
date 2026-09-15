# 当前激光净空回放交付

唯一当前安装包：[wfrl_blender-0.2.5.zip](wfrl_blender-0.2.5.zip)

SHA256：`dd1f9e9dc65105ad2118b7ab61914dcf62c18d931992eb73e9a9c8f2018deeea`

结果目录（相对项目根）：

- `results/lidar/packages/normal-v1.1`
- `results/lidar/packages/close-v1.1`

两包采用完整数值比较证据合同；READY不表示现场精度或无容差的收敛达标。插件包含离线读取器与证据校验模块。原始仿真不需在线。

旧 0.2.1 / 0.2.2 / 0.2.3 / 0.2.4 安装包保留为历史版本；旧 normal/close 数据仍保留在原工作区，没有随本次仓库更新重复上传。当前机器可读清单见 [lidar-delivery.json](lidar-delivery.json)。专用源码演示入口自动读取此清单；安装扩展后，将两个数据目录填入 Item → Camera 相机与演示 → 净空与误差对比 → 数据配置中的路径字段。

Mac 可在完整仓库中双击 `scripts/blender/打开净空雷达演示.command`；完整操作见[用户手册第 12 节](../docs/blender/用户使用手册.md#12-激光净空雷达第一次照着操作)。原始 `results/lidar/raw/`、本地验收过程报告和截图不包含在本次发布中，前端回放不依赖这些内容。

在项目根隔离校验实际ZIP与两个包：

```sh
python3 scripts/blender/verify_lidar_delivery.py dist/wfrl_blender-0.2.5.zip results/lidar/packages/normal-v1.1 results/lidar/packages/close-v1.1 --output /tmp/wfrl-delivery-new-check.json
```

输出路径须为新文件。此命令验证隔离ZIP读取，不代表Blender GUI或设备验收。
