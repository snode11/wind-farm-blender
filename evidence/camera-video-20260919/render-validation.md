# 固定机舱视频渲染验证（2026-09-19）

已使用独立 Blender 5.2.1 LTS 后台进程；未修改现有窗口、源结果包或重新运行 FAST.Farm。

## 原生验证

`native-render-regression-production/checks.json` 是最终代码的通过记录。任意仿真时刻直接驱动柔性几何；相机安装 basis 不变，数据采样器的世界相机变换与 Blender 最大矩阵元素误差 5.35e-6。检查 15/20/25 FPS 的900/1200/1500帧中心时间及快门边界，208辅助对象禁止所有渲染射线，线性0.5经过一次sRGB转换的误差5.80e-6。小图实际渲染、续作、配置不匹配拒绝、图像改动拒绝均通过。新增在 PNG 原子提交前模拟中断并验证自动恢复；逐帧 SHA 记录先于 PNG 提交，避免崩溃产生无法识别的完成帧。原生检查先注入 DWAA/OVERRIDE，验证渲染器强制 ZIP 无损 EXR 和 FOLLOW_SCENE，防止已保存场景继承有损中间文件或独立色彩覆盖。

命令：

```sh
WFRL_TEST_OUTPUT=evidence/camera-video-20260919/native-render-regression-production /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python blender_frontend/tests/blender/camera_video_regression.py
```

POSIX进程锁的并发拒绝、正常释放和强制终止后释放已通过，见 `process-lock.json`；Windows msvcrt 分支未在本机测试。

## 曝光样片与性能

| 规格 | Cycles空间样本×曝光样本 | 渲染时间/帧 |
|---|---|---|
| 1440p CPU |16×4|26.87 s|
| 4K CPU，25 ms快门 |32×8|296.56 s|
| 4K Metal，25 ms快门 |32×8|58.19 s|
| 4K Metal，1 ms快门 |32×8|50.59 s|
| 4K Metal，1 ms快门 |32×16|100.46 s|

设备检测到 Apple M5 Pro CPU / Apple M5 Pro 20核 Metal GPU。时间从场景准备后计算；主场景首次构建约数秒。性能仅适用于这台机器和样帧，不是整段耗时保证。1ms、8子样本母版约49 MiB/帧，1200帧估算约57 GiB。

117.75 s的B1扫过样帧已检查全图和原生分辨率叶尖裁剪。25ms快门配8子样本有明显离散多重边缘，不作为收敛成品。1ms快门的8与16子样本均未见该多重边缘；显示空间RGB平均绝对差0.00835，叶片ROI平均差0.00596，包含空间Monte Carlo噪声，见 `shutter-comparison.json`。默认曝光改为1ms，以8子样本作为首个短片候选；不能将单帧对比推广为全段、所有天气或其他快门时间的严格误差界。

采用显式快门中点求积：各时刻独立更新真实柔性运动，输出32位场景线性EXR，线性平均后仅一次固定视图变换到16位PNG。无原生程序化变形运动模糊兼容性假设，无相邻视频帧交叉淡化。DOF默认关闭，60m对焦距离与f/8记录为固定理想针孔参数；不模拟传感器噪声、畸变和未知支架振动。

基准探针发生在实现迭代中，保存的配置指纹反映当时版本；不能与新版本输出混帧续作，也不把其状态替代后续完整视频包验收。完整60秒渲染、编码、RTSP及30分钟流验证由对应独立记录证明，本文件不声明它们已完成。
