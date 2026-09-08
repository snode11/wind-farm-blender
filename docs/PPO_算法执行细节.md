# PPO 算法执行细节（风电场偏航控制项目）

> 环境：`Ablaincourt_Floris`（7 台风机）｜算法：PPO（Stable-Baselines3）｜后端：FLORIS 稳态尾流

---

## 一、项目依赖栈

```
wfcrl (conda env)
├── floris 3.5          ← 风场物理仿真（稳态尾流求解器，纯 Python）
├── wfcrl-env           ← Gymnasium/PettingZoo RL 接口（Inria/IFP）
├── stable-baselines3 2.3.2  ← PPO 实现
├── torch 2.13.0+cpu    ← SB3 后端（CPU 训练）
├── numpy 1.26.4        ← ★ 必须锁版本，sb3[extra] 会拉 numpy2.x 破坏 FLORIS
├── tensorboard 2.21    ← 训练指标可视化
└── matplotlib          ← 尾流场 + 时序图输出
```

**三者分工**：
- **Gymnasium**：接口协议（reset/step 签名标准），不含算法/环境
- **WFCRL**：提供「环境」——FLORIS 风场仿真，遵守 Gym 标准
- **SB3**：提供「算法」——PPO 等 RL 算法，也遵守 Gym 标准
- **sb3_wrapper.py**：修补 WFCRL 与 SB3 的三处接口不兼容

---

## 二、环境：FLORIS 稳态仿真

### 什么是稳态

FLORIS 每次 `step()` 不模拟尾流传播过程，而是直接用解析公式算出：
「如果当前偏航角永远保持，最终稳定下来的风况和功率是多少？」

- **每步独立**，不依赖上一步的尾流状态
- **瞬间生效**：改变偏航角，下一步立刻看到新风况（无传播延迟）
- **无湍流噪声**：reward 是干净的确定值

对比动态仿真（FAST.Farm）：尾流像烟飘到下游需要几十秒，有延迟和湍流抖动。

### 风场布局

```
来流风 →→→→ [T1] ~~尾流~~ [T2] ~~尾流~~ [T3] ...  共7台
上游风机     8 m/s        ↓6.2 m/s       ↓5.5 m/s
                     站在尾流里，风速被削弱
```

偏航控制目标：调整 T1 偏航角，把尾流"偏转"到 T2 旁边，T2 吃到更强的风，总功率提升。

### `env.step(yaw)` 的输出

```python
obs, reward, terminated, truncated, info = env.step({"yaw": action})
```

| 字段 | 类型 | 含义 |
|------|------|------|
| `obs["yaw"]` | (7,) | 执行后 7 台风机当前偏航角 |
| `obs["freewind_measurements"]` | (2,) | 上游来流 [风速 m/s, 风向°]，全场共享 |
| `obs["wind_speed"]` | (7,) | ★ 每台风机处局部风速（各不相同） |
| `obs["wind_direction"]` | (7,) | ★ 每台风机处局部风向（各不相同） |
| `reward` | float | 7 台总发电功率（标量，已 squeeze） |
| `info["power"]` | (7,) | 每台风机单独功率 |

**7 台风机局部风况不同**：上游风机接近来流值，下游越来越弱，这正是偏航优化的物理基础。

---

## 三、适配层：sb3_wrapper.py

WFCRL 与 SB3 有三处接口不兼容，`WFCRLSB3Wrapper` 逐一修复：

```
SB3 (PPO)  ⇄  WFCRLSB3Wrapper  ⇄  WFCRL WindFarmEnv (FLORIS)
              └─── 翻译三处不兼容 ───┘
```

| 问题 | WFCRL 实际返回 | SB3 期待 | 修复 |
|------|--------------|---------|------|
| `reset()` 缺 info | `obs` | `(obs, info)` | 补 `return obs, {}` |
| reward 是数组 | `np.array([8730.0])` | `float` | `float(np.asarray(r).squeeze())` |
| action_space 是 Dict | `Dict({"yaw": Box(7)})` | `Box(7,)` | 对外展平成 Box，step 时包回 Dict |

**观测空间保持 Dict 不动**——SB3 的 `MultiInputPolicy` 原生支持 Dict 观测。
额外提供 `get_inner_env()` 穿透 wrapper 拿 FLORIS 实例，用于画尾流场。

---

## 四、网络结构：Actor-Critic 双 MLP

`ppo_ablaincourt` **不是预训练基座**，而是 PPO 从零训练出的一对 MLP。
"PPO 是算法"（规定怎么更新参数），"MLP 是被训练的网络"。

```
Dict obs (4 个 key)
      │
      ▼
CombinedExtractor（无参数）    把每个 Box key 展平后拼接 → 23 维特征
      │
      ├──────────────┬──────────────┐
      ▼              ▼
  Actor MLP      Critic MLP        （SB3 v2 默认不共享主干）
  23→128→128     23→128→128         激活 = Tanh
      │              │
  action_net     value_net
  128 → 7        128 → 1
      │              │
      ▼              ▼
   μ (7,)         V(s) 标量
   + log_std(7)
```

- **CombinedExtractor**：不是 MLP，仅把 Dict 展平拼成 23 维向量，无可学习参数。actor/critic 共享此层。
- `MultiInputPolicy` vs `MlpPolicy` 唯一区别就是这个特征提取器（处理 Dict vs 单 Box），后面都是 MLP。

### 策略网络 π（Actor）—— 对角高斯分布

连续动作（7 维 yaw 增量）用对角高斯：

```
π(a|s) = N(a; μ_θ(s), σ²)

μ_θ(s)：actor MLP 根据状态算出 → 依赖 s（决策中心）
σ：log_std，7 个可学习参数    → 不依赖 s（全局探索强度）
a ~ N(μ, σ²)
```

**为什么是"对角"高斯**：7 维高斯本需 7×7 协方差矩阵（49 参数、须正定、难训）。
"对角"= 只保留对角线、非对角相关项设 0，等价于假设 7 个动作维度独立，
只需 7 个 σ，参数少且稳定。风机间协调靠 μ 共享状态 s 隐式实现。

**σ 不是固定值也不是 MLP 输出**：是独立的 `log_std` 参数（初始 log_std=0 → σ=1.0），
随训练由梯度自动调整。存 log 是为保证 σ>0（σ=exp(log_std)）。
TensorBoard `train/std` 记录它——训练收敛后 σ 下降；卡在 1.0 说明策略没学到东西。

- 训练：`a ~ N(μ,σ²)` 采样（带随机探索）
- 推理：`deterministic=True` 直接取 μ，丢掉随机性

### 价值网络 V（Critic）

```
23 维特征 → 128 → 128 → 1
输出标量 V(s)：从当前状态出发预期累计折扣回报
```

作用：提供 **advantage 的基线**。PPO 不直接用回报做梯度，而是用
`A = Q(s,a) − V(s)`，减去基线大幅降低梯度方差。
critic 只用于训练，部署时可扔掉。

**explained_variance**（判断训练健康度的头号指标）：
```
explained_variance = 1 − Var(return − V_pred) / Var(return)
= 1  → V 完美预测（本项目 VecNormalize 版 ≈ 0.99）
= 0  → V 和瞎猜均值一样差（无归一化版）
< 0  → 比瞎猜还差
```

---

## 五、优势 A（Advantage）与 GAE

### 直觉定义

```
A(s,a) = Q(s,a) − V(s)
         └实际这个动作的价值  └状态平均价值（基线，critic 输出）

A > 0：这动作比平均好 → 提高其概率
A < 0：比平均差       → 降低其概率
```

**为什么减基线**：若某状态所有动作回报都≈1000，直接用回报分不出好坏且方差大；
减去基线后（1010→+10，990→−10）凸显相对好坏，梯度方差骤降。这是 critic 存在的全部意义。

### GAE 计算（gamma=0.99, gae_lambda=0.95）

不显式算 Q，用 GAE 反向递推：

```
① TD 误差（单步）：δ_t = r_t + γ·V(s_{t+1}) − V(s_t)
② 指数加权累加：   A_t = δ_t + (γλ)·δ_{t+1} + (γλ)²·δ_{t+2} + ...
③ 训练目标：       return_t = A_t + V(s_t)   ← critic 的拟合目标
```

λ 控制视野：λ→0 只看一步（偏差大方差小），λ→1 看到幕尾（偏差小方差大），0.95 是标准折中。

**必须采满 n_steps 才能算**：GAE 反向递推，第 t 步优势依赖 t 之后的 δ。

---

## 六、损失函数：三部分组成

```python
loss = L_policy + 0.5·L_value + 0.005·L_entropy
       (actor)     (critic,vf_coef)  (探索,ent_coef)
```

### L_policy（Actor）—— 用 GAE 的 advantage A

```python
ratio = exp(log_prob_new − log_prob_old)          # 新旧策略概率比
L_policy = −min( ratio·A,  clip(ratio, 0.8, 1.2)·A )   # clip_range=0.2
```

### L_value（Critic）—— 用 GAE 的 return

```python
L_value = 0.5 · (V_pred − returns)²
```

### L_entropy —— 与奖励无关，鼓励探索

```python
L_entropy = −H(π)
```

**信号来源澄清**：
- 直接进 loss 的是 **GAE 的产物**（advantage → actor，return → critic）
- **reward** 只是 GAE 输入原料（经 δ = r + γV' − V 进入），不直接进 loss
- **Q 值** PPO 压根不算（用 V + GAE 隐式代替 A = Q − V）

### 关于 ratio 和 old/new（关键理解）

- **π_old = 采集这批数据时冻结的策略快照**（log_prob 当场存下，是定值）
- **π_new = 当前正在被梯度更新的策略**
- old **不是**"慢一步的策略"。每步更新时用同版 θ 采样又立刻算 ratio → ratio 恒=1，clip 失效
- PPO 靠"冻结 old + 对同批数据更新多次"让 new 逐步偏离 old，ratio 才 ≠ 1，clip 才有约束对象
- clip 目的：同批数据复用多次（80 次）时防止策略被推太猛

---

## 七、完整执行流程（一次 PPO 迭代）

### 阶段 0-1：环境重置 + obs 归一化

```python
obs = venv.reset()   # Dict, 每 key 前带 n_envs=1 维
# VecNormalize 对每个 key 独立归一化：
norm_obs[key] = clip((obs[key] − mean[key]) / sqrt(var[key]+1e-8), −10, 10)
```

**为什么必须 VecNormalize**：原始 obs 量纲差异大（风向~270、风速~8、yaw~0），
return 量级~1600（200步×8kW），网络初始输出≈0 → value 拟合不了 → explained_variance≈0 → 策略乱漂。
归一化后 explained_variance → 0.99。

### 阶段 2：前向传播（一步决策）

```
norm_obs → CombinedExtractor → features(23)
  → actor MLP → μ(7);  log_std → σ(7);  a ~ N(μ,σ²);  log_prob
  → critic MLP → V(s)
a 不经过 VecNormalize，直接 → env.step({"yaw": a}) → FLORIS 求解 → obs', reward
reward 过 VecNormalize(norm_reward=True) 后存 buffer
```

### 阶段 3：采集 rollout（重复 n_steps=1024 步）

每步存 6 个量进 RolloutBuffer：

| 字段 | 形状 | 用途 |
|------|------|------|
| observations | (1024, 23) | 更新时重新前向 |
| actions | (1024, 7) | 重算 log_prob_new → ratio |
| rewards | (1024,) | GAE 算 δ |
| episode_starts | (1024,) | GAE 截断（幕结束不向后累加）|
| values | (1024,) | critic 当时的 V(s)，GAE 用 |
| log_probs | (1024,) | 旧策略概率，算 ratio |

**为什么要采集 1024 步**（不是每步更新）：
1. PPO 是 on-policy，需批量样本降低梯度方差（策略梯度含期望 E[·]）
2. GAE 需时序上下文才能反向递推
3. clip 需要 old log_prob 且 new 相对 old 有偏离才有意义
- 太小：采样低效、方差大；太大：策略老化偏离 on-policy 假设。1024-2048 是经验折中。
- 1024 步 ≈ 5 幕（每幕 max_num_steps=200）

### 阶段 4：GAE 算 advantage + return

```python
for t in reversed(range(1024)):
    δ_t = rewards[t] + γ·values[t+1]·(1−done) − values[t]
    A_t = δ_t + γ·λ·(1−done)·A_{t+1}
advantages = (A − A.mean()) / (A.std() + 1e-8)   # 批内标准化，进一步降方差
returns = advantages + values
```

### 阶段 5：参数更新（n_epochs=10 × 8 mini-batch = 80 次梯度步/轮）

```python
# batch_size=128, 1024/128=8 个 mini-batch
# 每个 mini-batch：
μ_new,σ_new = actor(obs);  V_new = critic(obs)
log_prob_new = Normal(μ_new,σ_new).log_prob(action).sum(1)
ratio = exp(log_prob_new − log_prob_old)
L = L_policy + 0.5·L_value + 0.005·L_entropy
optimizer.zero_grad(); L.backward()
clip_grad_norm_(params, 0.5)      # max_grad_norm=0.5
optimizer.step()                  # Adam, lr=3e-4
# 同时更新 actor(μ网络+log_std) 和 critic(V网络)
```

TensorBoard `train/` 组记录：explained_variance / value_loss / policy_gradient_loss /
entropy_loss / approx_kl / clip_fraction / std 等（第一次梯度更新后才出现）。

### 阶段 6：循环 + 保存

清空 buffer → 回阶段 1。100k 步 ≈ 97 轮 rollout × 80 = 约 7760 次参数更新。

```python
model.save("checkpoints/ppo_ablaincourt")   # 网络权重 θ
venv.save("checkpoints/vecnormalize.pkl")    # obs/reward 的 mean/var（推理必须）
```

**推理**：raw obs → normalize_obs(用 pkl) → actor MLP → μ（deterministic，丢 σ）
→ {"yaw": μ} → FLORIS → 新尾流场 + 功率

---

## 八、超参数对照表

| 超参 | 值 | 作用位置 |
|------|-----|---------|
| learning_rate | 3e-4 | Adam 步长 |
| n_steps | 1024 | 每次更新前采集步数（rollout buffer） |
| batch_size | 128 | mini-batch，1024/128=8 个 |
| n_epochs | 10 | 每批数据复用次数 |
| gamma | 0.99 | 折扣因子 |
| gae_lambda | 0.95 | GAE 偏差/方差权衡 |
| clip_range | 0.2 | policy loss clip 边界 [0.8,1.2] |
| ent_coef | 0.005 | 熵项权重 |
| vf_coef | 0.5 | value loss 权重 |
| max_grad_norm | 0.5 | 梯度裁剪 |
| net_arch | [128,128] | actor/critic 隐藏层 |
| policy | MultiInputPolicy | 处理 Dict 观测 |

---

## 九、运行命令

```bash
source activate wfcrl
cd "D:\project\wind farm RL"

# 训练（后台）
nohup bash -c 'source activate wfcrl && python -u train_ppo.py --timesteps 100000' > train.log 2>&1 &

# 监控
grep -E "total_timesteps|explained_variance" train.log | tail -6

# TensorBoard（explained_variance 在 train/ 组，不在 rollout/）
tensorboard --logdir logs/ppo_wf     # → http://localhost:6006

# 评估 + 出图（固定风况做 A/B 对比）
python eval_visualize_ppo.py         # → eval_outputs/{wakefield_*.png, ppo_timeseries.png}
```

---

## 十、当前进度与待办

- ✅ FLORIS 后端部署（mpi4py / openfast_toolbox 改 optional import，numpy 锁 1.26.4）
- ✅ SB3 适配层（三处不兼容修复）
- ✅ PPO 训练 100k 步 + VecNormalize，explained_variance ≈ 0.99
- ✅ 尾流场 + 时序可视化
- ❌ **FAST.Farm 后端未实现**（需 MS-MPI + OpenFAST 编译链，故意跳过）
  - 典型工作流：FLORIS 快速训练 → FAST.Farm 高保真验证
  - 启用需：装 MS-MPI + mpi4py、编译 FAST.Farm 可执行文件、撤销 optional import 补丁、ENV_ID 改 `Ablaincourt_Fastfarm`

