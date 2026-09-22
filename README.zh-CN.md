# JevTree

**把 Jev 的快速局部判断，组合成可执行的长程概率树。**

[English](README.md)

![JevTree 概率决策树](assets/jevtree-probability-tree-concept.png)

JevTree 是一个小型、typed 的 Python runtime。任务 adapter 负责状态、合法动作、确定性转移和
终点验证；JevTree 负责批量询问 Jev、组合路径联合概率、合并等价状态、记录未探索质量，并根据
下游 outcome mass 选择动作，而不是逐步贪心。

它不是生成式 CoT，也不需要另一个 LLM 在运行时修正动作。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

## 无 API key 冒烟测试

```bash
jev-tree game24 1 2 3 4 \
  --provider heuristic \
  --output /tmp/jev-tree-smoke.json
```

## 接入真实 Jev

```bash
cp .env.example .env
# 在 .env 中填写 TYPESAFE_API_KEY，不要提交该文件。
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
提交新的 Game24 任务时才会在服务端读取 `TYPESAFE_API_KEY`。

如果直接打开 `src/jevtree/web/index.html`，页面会自动连接
`http://127.0.0.1:8765`；其他地址可用 `?api=http://127.0.0.1:9000` 指定。远程静态页面需要把
它的 origin 加入服务端逗号分隔的 `JEVTREE_ALLOWED_ORIGINS`；默认不会开放通配 CORS。

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

完整例子见 [`examples/custom_workflow.py`](examples/custom_workflow.py)，接口与安全约束见
[`docs/ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md)。

| 模式 | 适用范围 | 可以声称的概率语义 |
|---|---|---|
| exact tree | 可完整枚举的小型有限任务 | 完整联合叶分布 |
| complete graph | 有等价状态或循环 | 完整展开时的有限时域状态图 |
| adaptive graph | provider 调用有硬预算 | 已探索图上的 success / failure / unresolved mass |

## 当前边界

当前版本面向有限、确定、合法动作可枚举且有可执行 verifier 的任务。对于网页、API 或机器人，建议
采用 `观察 → 建短树 → 选一个 Pareto 动作 → 风险守卫 → 只执行一步 → 重新观察与规划`。不可逆
操作必须增加人工或策略审批。adaptive 模式保留 `unresolved`，不会把被裁剪分支伪装成完整分布。

Apache-2.0。
