# JevTree

**一个 Jev-native 的长程概率规划 harness：把快速的局部判断，组织成可执行、可审计的多步决策图。**

[English](README.md)

![JevTree：从局部判断到长程概率决策树](assets/jevtree-readme-hero-final.png)

Jev 擅长快速回答“当前应该选择什么”。但在长程任务中，眼前概率最高的动作并不一定通向
全局最优结局。JevTree 的核心假设是：**Jev 的快速局部判断，可以被组织成真正参与执行的
长程概率图，而不只是逐步贪心。**

JevTree 是一个 typed Python runtime，而不是生成式 Chain-of-Thought，也不依赖另一个 LLM
在运行时修正 Jev。任务 adapter 负责状态、合法动作、状态转移与终点验证；JevTree 负责批量
获取局部动作分布、组合路径联合概率、合并等价状态、保存未探索质量，并在 Pareto 前沿上选择动作。

## JevTree 如何工作

```text
当前状态
   ↓
前向展开候选未来
   ↓
Jev 批量判断 P(action | state)
   ↓
沿路径组合联合概率，并合并等价状态
   ↓
聚合 success / failure / unresolved mass
   ↓
在 local probability、downstream quality 与 risk 的 Pareto 前沿上决策
```

具体来说，JevTree 提供：

- **Exact probability tree**：完整枚举规模可控的有限任务；
- **Merged probability graph**：合并重复状态，并处理有限时域循环；
- **Adaptive graph search**：在 provider 预算内优先探索高价值状态；
- **Pareto action summary**：同时记录局部概率、下游成功质量、风险与未探索质量；
- **TypeSafe/Jev backend**：以 typed `Choice` 请求批量获取局部动作分布；
- **可重放证据**：保存节点、边、概率质量、请求、token、延迟与 outcome；
- **动态树 Demo**：候选分支随规划生长，推荐路径按真实计算进度逐段点亮。

## 受控实验

### 同一个 Jev，改变决策方式

主实验让 JevTree 与 local-greedy policy 共享同一批 Jev 概率判断。两者使用相同的状态、合法
动作和 provider evidence，区别只在于是否组合未来路径质量。因此，这组对照隔离验证的是
**harness 如何使用同一概率空间**，而不是更换了更强的底层模型。

| Benchmark | Local Jev greedy | JevTree | 绝对提升 |
|---|---:|---:|---:|
| Game24 official 100 | 7/100 | **100/100** | **+93 pp** |
| MiniGrid unseen 100 | 70/100 | **93/100** | **+23 pp** |

200 个任务中 provider error 为 0，概率质量守恒最大误差为 `1.33e-15`。这支持一个有限但清楚
的结论：在这些可验证的多步任务里，对局部判断进行前向组合和 outcome 回传，明显优于逐步贪心。

### 与 Opus 4.8 的小规模辅助对照

我们还在两个固定的 20 条切片上，让 Opus 4.8 以完整当前状态执行闭环 action loop：

| 固定切片 | JevTree | Opus 4.8 | 记录墙钟 |
|---|---:|---:|---:|
| Game24，20 题 | **20/20** | 3/20 | 75.5s vs 213.1s（JevTree 约快 2.82×） |
| MiniGrid，20 seeds | **18/20** | 9/20 | 234.6s vs 375.8s（记录值约快 1.60×） |

这只是辅助证据，而不是通用模型排名。MiniGrid 的 Opus 记录包含一次 180 秒 CLI 传输超时，
两侧的批处理结构也不同；因此这里报告的是可复核的端到端记录值，不将其解释为 provider 的
普遍速度差异。当前 JevTree 搜索使用的 provider token 也明显更多：它的优势是显式搜索带来的
成功率和可审计性，而不是已经实现了 token 最优。

## 适用场景

JevTree 当前最适合以下共同特征明显的任务：状态可以结构化表示、合法动作可以枚举、状态转移
可以执行或预测、结果可以验证。潜在方向包括：

- 购物搜索、约束筛选与多商品比较；
- 网页表单、后台工作流与客服 API 编排；
- 游戏控制与有限动作策略规划；
- coding-agent 的工具选择、修改—测试—回退流程；
- 带风险守卫的机器人或业务流程决策。

真实网页或工具调用建议采用 receding-horizon controller：

```text
观察真实状态 → 建立 2–4 步安全候选图 → Jev 批量判断
→ Pareto 选择 → 风险守卫 → 只执行一步 → 重新观察并规划
```

不可逆操作必须增加人工或策略审批。

## 安装

需要 Python 3.10+。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

## 60 秒无 Key 冒烟测试

```bash
jev-tree game24 1 2 3 4 \
  --provider heuristic \
  --output /tmp/jev-tree-smoke.json
```

输出包含终点叶分布、概率质量检查、Pareto 根动作与最终策略路径。

## 接入真实 Jev

```bash
cp .env.example .env
# 在 .env 中填写 TYPESAFE_API_KEY；不要提交该文件。
set -a; source .env; set +a

jev-tree game24 1 2 3 4 \
  --provider typesafe \
  --model jev-latest \
  --question-batch-size 128 \
  --include-edges \
  --output /tmp/jev-tree-live.json
```

## 启动动态 Demo

```bash
jev-tree-demo --host 127.0.0.1 --port 8765
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。没有 key 也能播放 recorded tree；只有
提交新的 Game24 任务时才会在服务端读取 `TYPESAFE_API_KEY`，密钥不会进入浏览器。

如果直接打开 `src/jevtree/web/index.html`，页面会连接 `http://127.0.0.1:8765`；其他后端可用
`?api=http://127.0.0.1:9000` 指定。远程静态页面需要将 origin 加入逗号分隔的
`JEVTREE_ALLOWED_ORIGINS`；服务端默认不会开放通配 CORS。

Docker：

```bash
docker compose up --build
```

## 接入自己的任务

实现 `FiniteDecisionProblem` 的八个接口：

- `initial_state`
- `common_state()`
- `decision_key(state)`
- `make_query(state, query_id)`
- `apply(state, action_key)`
- `is_terminal(state)`
- `terminal_outcome(state)`
- `state_payload(state)`

完整例子见 [`examples/custom_workflow.py`](examples/custom_workflow.py)，接口、不变量和安全约束见
[`docs/ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md)。

| Runtime | 适用范围 | 可以声称的概率语义 |
|---|---|---|
| exact tree | 可完整枚举的小型有限任务 | 完整联合叶分布 |
| complete graph | 有等价状态或循环 | 完整展开时的有限时域状态图 |
| adaptive graph | provider 调用有硬预算 | 已探索图上的 success / failure / unresolved mass |

## 当前边界

- 当前结果更接近机制验证，尚未覆盖真实购物或网页 benchmark；
- 环境需要有限、确定、全状态可见，或至少拥有可靠的短期状态预测器；
- 完整 tree/graph 可能消耗大量 token，adaptive search 仍在优化；
- `success mass` 是 harness 内部的决策质量，不等于现实世界中经过校准的成功概率；
- adaptive 模式只描述已探索图，裁剪分支始终保留为 `unresolved`，不会伪装成完整叶分布。

## 安全

- 将 `TYPESAFE_API_KEY` 保存在环境变量或被忽略的 `.env` 中；
- 浏览器不会收到 API key，Demo 错误也会对 key 做脱敏；
- 不要在没有认证、限流和 TLS 的情况下把本地 Demo 直接暴露到公网；
- 对付款、提交、删除等不可逆动作设置人工或策略 gate。

## License

Apache-2.0，见 [`LICENSE`](LICENSE)。
