"""Pure training numerics run without simulator or MPI imports."""
import os
from pathlib import Path
import subprocess
import sys


def test_numeric_training_runs_when_simulator_imports_are_unavailable():
    source = '''
import sys
class NoSimulators:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'wfcrl', 'mpi4py', 'pettingzoo',
                                      'pyvista', 'pyvistaqt', 'PySide6', 'vtk'}:
            raise AssertionError('Unexpected simulator dependency: ' + fullname)
sys.meta_path.insert(0, NoSimulators())
import torch
from scripts.train.train_fastfarm import RecurrentCritic, compute_gae_trunc
from wfrl.mappo_central import Actor
from wfrl.studio.trainer import Trainer
from wfrl.rewards import make_shaper
import numpy as np
values, hidden = RecurrentCritic(3, hidden=4)(torch.zeros(1, 2, 3))
assert values.shape == (1, 2) and hidden.shape == (1, 1, 4)
assert torch.isfinite(values).all()
assert Actor(3, 1).dist(torch.zeros(2, 3)).mean.shape == (2, 1)
assert make_shaper('level')(3.) == 3.
adv, ret = compute_gae_trunc(np.array([1., 2.]), np.array([3., 4.]),
                           np.array([8., 9.]), np.array([1., 0.]), .5, 1.)
np.testing.assert_allclose(adv, [2., 2.5])
np.testing.assert_allclose(ret, [5., 6.5])
'''
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', source], cwd=root,
                            env=environment, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
