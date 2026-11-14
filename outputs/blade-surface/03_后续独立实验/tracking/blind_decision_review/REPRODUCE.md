# 重现自造fixture

本目录FROZEN protocol均为synthetic fixture，不是真实run protocol，不得用于正式准入。无需source/private/WideEval或实际评分输入。两个subject_snapshot记录修复前后原字节。

使用只读既有blade_recon/.venv-trial/bin/python与macOS sandbox-exec，不安装/联网。原执行命令为：

```sh
cd 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/tracking/blind_decision_review'
sandbox-exec -f review.sb 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' synthetic_checks.py
sandbox-exec -f review.sb 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' synthetic_checks_v2.py
```

输出目录exist_ok=False，已有fixture再次运行会拒绝覆盖。复跑时在tracking下创建新的同层review目录，只复制两个synthetic_checks、两个subject_snapshot与review.sb；把新review.sb内的允许review绝对路径改成新目录，然后从新目录运行上述两命令。保持其与repo的相对层级，Python审计ROOT/REPO即可自动正确。保留原目录与原输出。scripts先制造fixture，然后仅读取self-made目录；v2验证30个状态、FAILED主值null、完整36/252与exit返回值。

正式CLI有metrics/protocol/observations/integrity/common-freeze/out六个mandatory参数。合法合成样例的真实CLI结果保存于cli_pass_v2/decision.json；缺common-freeze的argparse退出2。正式integrity须status=PASS且common_freeze_before_any_test_scoring=true；脚本不靠mtime推断评分前冻结。schema/文件/数值或primary投影失效写FAILED/null并退出1；有效但未达准入门槛退出0、NO_CONTROLLED或INCONCLUSIVE。原fixture的非null参数不可移作正式阈值。
