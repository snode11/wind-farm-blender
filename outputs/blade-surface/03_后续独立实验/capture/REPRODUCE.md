# 捕获复现

在真实Git根 `REPOSITORY_ROOT` 执行。Blender5.2.1 LTS，factory启动，独立 `capture/config`；无需安装、训练或共享环境变更。

`capture_configuration_freeze.json` 保存全部相机、采样和源码SHA。`frozen_code/` 是字节身份副本；脚本依赖标准capture目录位置，应先核对原 `capture/*.py` SHA再从原路径执行，不直接在frozen_code改变相对路径语义。每个新捕获label必须不存在；相同label不会覆盖。

2026-10-06已实际执行以下原冻结wrapper命令，完成三固定窗48张RGB：

```sh
blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-exec-a01/capture/run_formal_capture_wrapper.py --global-freeze 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/experiment_protocol.json'
```

global SHA `b295e6437e9abfd89ffcc938eb1931ebedd6c18857f1292c45283358f7e84926`；wrapper SHA `f26f3fb5d9dbfc2efa9dcc73885995075e496ebe44bfa6e7f407416fd90bb926`。正式label `formal_test01/02/03` 已存在，再执行会拒绝覆盖；命令作为实际运行证据保留，后续重算须使用另一个经授权的新输出run/label并保留旧失败。

wrapper校验7脚本、原blend与6source模块SHA，逐窗capture双路16RGB，再运行冻结RGB junction/mask/palette-v2。对应 `.log`、`formal_capture_execution.json`、private read_audit与commit evidence是实跑证据。相机MODEL光心/aim每帧保持同一设计值，World-up规则形成逐帧已知外参；求解取各帧 `calibration.json` K/T，不把首帧 `cameras.json`摘要当全窗静态标定。

```sh
BLENDER_USER_CONFIG="$PWD/outputs/blade-surface-20261006-exec-a01/capture/config" /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python "$PWD/outputs/blade-surface-20261006-exec-a01/capture/capture_distributed.py" -- --times '143.0,143.1,143.2,143.3,143.4,143.5,143.6,143.7' --label replay_test01_new --save-copy
```

上述仅在root整体冻结后运行正式复现。开发已运行label和逐次命令可由各 `.log`、capture report与configuration取得。源场景只读；公开input在capture/label/public_input，所有评价真值和源copy在private_evaluation/label。

输入RGB-only后处理命令（替换成新label路径；在正式协议中四组共享同一产物）：

```sh
blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-exec-a01/capture/rgb_junction_candidates.py --input outputs/blade-surface-20261006-exec-a01/capture/replay_test01_new/public_input
blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-exec-a01/capture/prepare_rgb_masks.py --input outputs/blade-surface-20261006-exec-a01/capture/replay_test01_new/public_input
blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-exec-a01/capture/repair_rgb_palette_transitions_v2.py --input outputs/blade-surface-20261006-exec-a01/capture/replay_test01_new/public_input
```

逐图审核status不得伪造。候选JSON只能在明确Codex审核后形成冻结annotations；不能把private vertex投影复制为annotations。标记位置、源真值、WideEval与完整source calibration不交solver/tracking。

mask `historical_pre_repair_frames`是原始审计，final canonical SHA见 `final_frames`；不要混用。保留旧失败与前后overlay。完整数据不会自动证明几何增益；评价必须等重建哈希冻结后由独立权限执行。

root实际审核3×96 RGB crop并授权后，`freeze_formal_rgb_annotations.py`只将公开RGB候选转换为开发v1 schema；每窗 `rgb_junction_review/review_freeze.json` 保存candidate/sheet SHA、planned时序和12ID、缺失null规则。root实际mask review后，最终全量公开SHA写入 `formal_testXX/public_hashes_after_annotations.json`，交付入口 `formal_public_delivery.json`。visual-only `make_formal_mask_review_sheets.py`生成review图，不修改输入/算法。

root独立观测gate判定三窗均FAILED，现有公开输入继续用于固定组gated诊断；annotation/mask冻结不再修改，不重选测试时序。此处只记录root状态，不读取或回灌源点误差/坐标。最终实际raw RGB161张，正式双路产品64张，详见 `resource_ledger.json` 与 `raw_rgb_ledger_final_capture.json`；正式前113+48 PLANNED历史账保留。
