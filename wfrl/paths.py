"""仓库内所有输出目录的唯一定义处。

从 ``__file__`` 反推仓库根，因此脚本在任何 cwd 下运行结果都落在同一处 ——
以前 ``runs/`` / ``figs/`` 都是 cwd 相对路径，换个目录跑就会另起一套输出。

约定：
  results/   跑出来的东西（可删可重建）
  slides/figs/  进 LaTeX 的图，和 introduction_slides.tex 放在一起，
                整个 slides/ 目录可直接打包上传 Overleaf
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESULTS = os.path.join(ROOT, "results")
RUNS = os.path.join(RESULTS, "runs")            # 训练历史 json / 曲线 png / 实验产物
CKPT = os.path.join(RESULTS, "checkpoints")     # 模型权重
LOGS = os.path.join(RESULTS, "logs")            # tensorboard / monitor
EVAL = os.path.join(RESULTS, "eval_outputs")    # 评估期的流场图

FIGS = os.path.join(ROOT, "slides", "figs")     # 汇报图

for _d in (RESULTS, RUNS, CKPT, LOGS, EVAL, FIGS):
    os.makedirs(_d, exist_ok=True)
