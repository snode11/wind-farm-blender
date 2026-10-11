# 复现

从许可目录执行，禁止把 cwd 放在被 sandbox 拒绝的仓库根。

```sh
cd 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/tracking'
sandbox-exec -f tracking.sb ../runtime_tracking/venv/bin/python run_cotracker.py old_short/case.json --device mps
sandbox-exec -f tracking.sb ../runtime_tracking/venv/bin/python run_cotracker.py new_short/case.json --device mps
sandbox-exec -f tracking.sb ../runtime_tracking/venv/bin/python run_cotracker.py new_long32/case.json --device mps
sandbox-exec -f tracking.sb ../runtime_tracking/venv/bin/python run_cotracker.py keyframe_restart8/case.json --device mps
sandbox-exec -f tracking.sb ../runtime_tracking/venv/bin/python evaluate_tracking.py
```

复现前保留已有 result 到新的复现目录，或复制 case 到新目录，避免覆盖本次记录。权重、代码 revision、许可、完整依赖锁均在 `provenance/`；本 run runtime 隔离。推理仅需 case 列出的 RGB、annotations 的二维首个可见 query；审核其他帧只用于结果误差统计，不反馈模型。

`automatic_annotations.json` 使用原始图像像素，visibility_bool 与 confidence 不互换。每条轨迹全部帧均保存；不可删去错误/失踪结果后改变分母。`tracks.npz` 同时保存原始和工作坐标、query、predictor bool、模型 visibility/confidence 与覆盖 query 前的原始模型坐标。无需安装 Blender，不读 source 或 WideEval，不上传图像。

独立评价读取本次固定 `evaluation/frozen_inference_outputs.json`，任何保存推理结果SHA改变会失败。重跑推理请用复制后的新case目录和新的结果冻结清单，不覆盖原清单，不调旧成绩。`evaluate_tracking.py` 仅比较保存的自动二维结果和Codex审核RGB参考，不读取几何/source/WideEval。8vs32共同前8帧decoded RGB逐像素完全一致，原PNG元数据/文件SHA不同，具体证据在 `evaluation/paired_input_identity.json`。

固定长32=121.2–124.3s；固定短8=121.2–121.9s；独立restart8=123.6–124.3s。原32的queries均t=0，restart M00–10 t=0、M11 t=1；restart前未知样本留在96分母。主误差分别排除官方覆盖的query点。`new_long32/input/reviewed_annotations.json` 的完整384参考经root四页RGB审核，null/false保留，不以源真值填充。
