"""Interactive PPO 的联合动作目标；只计算固定张量，不启动训练或仿真器。"""

import math

import numpy as np
import pytest
import torch
from torch import nn
from torch.distributions import Normal

from wfrl.studio.trainer import Trainer


class _TabularActor(nn.Module):
    """每个时间步与机组有独立参数，便于检查各动作分量的梯度。"""

    def __init__(self, shape):
        super().__init__()
        self.mean = nn.Parameter(torch.zeros(shape, dtype=torch.float64))
        self.log_std = nn.Parameter(torch.zeros(shape, dtype=torch.float64))

    def dist(self, obs):
        steps = obs[:, 0, 0].long()
        return Normal(self.mean[steps], self.log_std[steps].exp())


class _ZeroCritic(nn.Module):
    def __init__(self):
        super().__init__()
        self.value = nn.Parameter(torch.zeros((), dtype=torch.float64))

    def v(self, state):
        return self.value.expand(state.shape[0], 1)


def _case(component_ratios, *, ent_coef=0.0, mb_steps=None):
    ratios = torch.as_tensor(component_ratios, dtype=torch.float64)
    steps, agents, _ = ratios.shape
    trainer = Trainer.__new__(Trainer)
    trainer.actor = _TabularActor(ratios.shape)
    trainer.critic = _ZeroCritic()
    trainer.hp = dict(clip=0.2, mb_steps=mb_steps or steps, n_epochs=1,
                      ent_coef=ent_coef, vf_coef=0.5, max_grad=100.0)
    obs = np.broadcast_to(np.arange(steps)[:, None, None],
                          (steps, agents, 1)).astype(np.float64)
    act = np.ones(ratios.shape, dtype=np.float64)
    with torch.no_grad():
        dist = trainer.actor.dist(torch.as_tensor(obs))
        old_logp = (dist.log_prob(torch.as_tensor(act)).sum(-1)
                    - ratios.log().sum(-1)).numpy()
    adv = np.linspace(1.0, -1.0, steps)
    ret = np.linspace(-0.5, 0.5, steps)
    optimizer = torch.optim.SGD(
        list(trainer.actor.parameters()) + list(trainer.critic.parameters()),
        lr=0.0,
    )
    return trainer, (obs, act, old_logp, adv, ret, optimizer)


def test_joint_ratio_multiplies_action_dimensions_without_merging_agents():
    trainer, args = _case([
        [[1.5, 0.8], [1.0, 1.0]],
        [[1.0, 1.0], [1.0, 1.0]],
    ])

    stats = trainer._update(*args)

    # 四个机组样本的代理值是 [1.2, 1, -1, -1] × 归一化优势幅值。
    scale = 1.0 / (math.sqrt(4.0 / 3.0) + 1e-8)
    assert stats["pg"] == pytest.approx(-0.05 * scale, abs=1e-12)
    # 第二台机组仍有自己的未裁剪梯度，不参与第一台的联合动作概率。
    torch.testing.assert_close(
        trainer.actor.mean.grad[0, 1],
        torch.full((2,), -scale / 4.0, dtype=torch.float64),
    )


def test_yaw_only_matches_scalar_clipping_and_gradients():
    trainer, args = _case([
        [[1.1], [0.9]],
        [[1.3], [0.7]],
    ])

    stats = trainer._update(*args)

    scale = 1.0 / (math.sqrt(4.0 / 3.0) + 1e-8)
    # 代理值 [1.1, 0.9, -1.3, -0.8]；负优势、ratio=0.7 的分支被裁剪。
    assert stats["pg"] == pytest.approx(0.025 * scale, abs=1e-12)
    expected = torch.tensor([[[-1.1], [-0.9]], [[1.3], [0.0]]],
                            dtype=torch.float64) * (scale / 4.0)
    torch.testing.assert_close(trainer.actor.mean.grad, expected)


def test_joint_clipping_handles_both_advantage_signs_and_all_action_gradients():
    trainer, args = _case([
        [[1.3, 1.1], [0.5, 1.0], [1.1, 1.0]],
        [[0.7, 1.0], [1.4, 1.0], [0.9, 1.0]],
    ])

    stats = trainer._update(*args)

    scale = 1.0 / (math.sqrt(6.0 / 5.0) + 1e-8)
    # 代理值 [1.2, 0.5, 1.1, -0.8, -1.4, -0.9]，先联合再裁剪。
    assert stats["pg"] == pytest.approx(0.05 * scale, abs=1e-12)
    expected = torch.tensor([
        [[0.0, 0.0], [-0.5, -0.5], [-1.1, -1.1]],
        [[0.0, 0.0], [1.4, 1.4], [0.9, 0.9]],
    ], dtype=torch.float64) * (scale / 6.0)
    torch.testing.assert_close(trainer.actor.mean.grad, expected)


@pytest.mark.parametrize("action_dim", [1, 2, 3])
def test_entropy_is_joint_per_agent_and_preserves_each_action_gradient(action_dim):
    ent_coef = 0.2
    trainer, args = _case(np.ones((2, 2, action_dim)), ent_coef=ent_coef)

    stats = trainer._update(*args)

    assert stats["ent"] == pytest.approx(
        action_dim * 0.5 * math.log(2.0 * math.pi * math.e), abs=1e-12,
    )
    # a=1, mu=0, std=1 时 log-prob 对 log_std 的导数为 0，仅留下熵梯度。
    torch.testing.assert_close(
        trainer.actor.log_std.grad,
        torch.full((2, 2, action_dim), -ent_coef / 4.0, dtype=torch.float64),
    )


@pytest.mark.parametrize("steps, agents, action_dim, mb_steps", [
    (1, 1, 1, 1),
    (1, 1, 3, 1),
    (1, 3, 3, 1),
    (3, 1, 3, 2),
    (5, 2, 3, 2),
])
def test_finite_inputs_keep_losses_and_gradients_finite(
    steps, agents, action_dim, mb_steps,
):
    trainer, args = _case(np.ones((steps, agents, action_dim)),
                          ent_coef=0.2, mb_steps=mb_steps)

    stats = trainer._update(*args)

    assert all(math.isfinite(value) for value in stats.values())
    for parameter in list(trainer.actor.parameters()) + list(trainer.critic.parameters()):
        assert parameter.grad is not None
        assert torch.isfinite(parameter.grad).all()
    if steps == 1:
        assert stats["pg"] == 0.0


@pytest.mark.parametrize("shape", [(2, 2, 3), (2, 2, 1), (2,), (2, 1)])
def test_old_log_probability_requires_one_value_per_step_and_agent(shape):
    trainer, args = _case(np.ones((2, 2, 3)))
    obs, act, _, adv, ret, optimizer = args

    with pytest.raises(ValueError, match="shape \\(steps, agents\\)"):
        trainer._update(obs, act, np.zeros(shape), adv, ret, optimizer)
