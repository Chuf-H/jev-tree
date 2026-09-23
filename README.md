# JevTree

**Turn fast local Jev judgments into an executable multi-step probability tree.**

[简体中文](README.zh-CN.md)

![JevTree: from fast local judgment to an executable probability tree](assets/jevtree-readme-hero-final.png)

JevTree is a small, typed Python runtime for finite multi-step decision problems. Your adapter defines
states, legal actions, transitions, and terminal verification. JevTree asks Jev for local action
distributions, composes them into path probabilities, merges equivalent states, tracks unresolved mass,
and selects actions using downstream outcome mass instead of one-step greediness.

It is a planner runtime—not generated chain-of-thought and not an LLM wrapper.

## Why JevTree

Jev is good at answering “what should I choose now?” quickly. In a long-horizon task, however, the
highest-probability action at the current step may not lead to the best final outcome. JevTree turns
fast local judgments into a probability graph that directly participates in execution:

1. expand multiple candidate futures from the current state;
2. ask Jev for batched local distributions, `P(action | state)`;
3. compose joint path mass and merge equivalent states;
4. aggregate success, failure, and unresolved mass;
5. select from the Pareto frontier of local probability, downstream quality, and risk.

The adapter remains responsible for legal actions, state transitions, terminal verification, and safety
gates. Jev becomes the decision engine; JevTree provides the long-horizon probability runtime.

## Controlled evidence

### Same Jev evidence, different decision policy

The primary comparison reuses the same Jev probability judgments for both policies. The local-greedy
ablation chooses the highest-probability next action; JevTree composes future path quality and propagates
verified outcomes. This isolates how the harness uses one probability space instead of changing the
underlying model.

| Benchmark | Local Jev greedy | JevTree | Absolute gain |
|---|---:|---:|---:|
| Game24 official 100 | 7/100 | **100/100** | **+93 pp** |
| MiniGrid unseen 100 | 70/100 | **93/100** | **+23 pp** |

Across the 200 tasks, there were no provider errors and the maximum probability-mass conservation error
was `1.33e-15`.

### Small paired comparison with Opus 4.8

As secondary evidence, Opus 4.8 ran a closed-loop action policy on two fixed 20-task slices with the full
current state and legal actions.

| Fixed slice | JevTree | Opus 4.8 | Recorded wall time |
|---|---:|---:|---:|
| Game24, 20 tasks | **20/20** | 3/20 | 75.5s vs 213.1s (2.82× shorter for JevTree) |
| MiniGrid, 20 seeds | **18/20** | 9/20 | 234.6s vs 375.8s (1.60× shorter as recorded) |

This is not a general model ranking. The MiniGrid Opus run includes one 180-second CLI transport timeout,
and the systems use different batching structures. These are reproducible end-to-end recorded times, not
a universal provider-speed claim. JevTree also used substantially more provider tokens in these runs: its
current advantage is search reliability and auditability, not token optimality.

## Where this could go

The runtime is intended for tasks with structured state, enumerable legal actions, executable or
predictable transitions, and a verifier. Promising directions include shopping and constraint filtering,
web forms, customer-service API workflows, game control, and coding-agent tool scheduling.

## What is included

- **Exact probability trees** for small finite tasks.
- **Merged probability graphs** for tasks with repeated states or cycles.
- **Budgeted adaptive search** that explores high-mass states first.
- **Pareto action summaries** over local probability, downstream success, and unresolved mass.
- **TypeSafe/Jev backend** with batched typed `Choice` calls.
- **Offline backend** for tests and zero-key smoke runs.
- **Replayable traces** with token, request, latency, mass, branch, and outcome records.
- **Animated local demo** plus a production-friendly Docker image.

## Install

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest
```

## Sixty-second smoke test—no API key

The offline backend validates installation and plumbing:

```bash
jev-tree game24 1 2 3 4 \
  --provider heuristic \
  --output /tmp/jev-tree-smoke.json
```

The output contains the terminal leaf distribution, probability-mass check, Pareto root actions, and
the selected policy.

## Run with Jev

Create a local environment file from the safe template:

```bash
cp .env.example .env
# Edit .env and set TYPESAFE_API_KEY. Never commit it.
set -a; source .env; set +a
```

Then run a live exact tree:

```bash
jev-tree game24 1 2 3 4 \
  --provider typesafe \
  --model jev-latest \
  --question-batch-size 128 \
  --include-edges \
  --output /tmp/jev-tree-live.json
```

## Launch the visual demo

```bash
jev-tree-demo --host 127.0.0.1 --port 8765
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The recorded tree works without a key. Live
Game24 submissions require `TYPESAFE_API_KEY`; the key stays server-side.

If you open `src/jevtree/web/index.html` directly, the page will look for the local API at
`http://127.0.0.1:8765`. A different backend can be selected with
`?api=http://127.0.0.1:9000`. Add non-local browser origins to the comma-separated
`JEVTREE_ALLOWED_ORIGINS` environment variable; wildcard CORS is intentionally disabled.

Docker:

```bash
cp .env.example .env
# Set TYPESAFE_API_KEY in .env if you want live requests.
docker compose up --build
```

## Use the Python API

```python
from jevtree.game24 import Game24Problem
from jevtree.probability_tree import derive_policy_path, enumerate_probability_tree
from jevtree.providers import TypeSafeBackend

problem = Game24Problem([1, 2, 3, 4])
backend = TypeSafeBackend(model="jev-latest")

try:
    result = enumerate_probability_tree(
        problem,
        backend,
        max_depth=3,
        question_batch_size=128,
        exploration_epsilon=0.01,
    )
    policy = derive_policy_path(result.leaves, mode="downstream_success")
    print(policy)
    print(result.outcome_probability)
    print(result.mass_error)
finally:
    backend.close()
```

## Bring your own task

Implement the typed `FiniteDecisionProblem` contract:

```python
class FiniteDecisionProblem(Protocol[StateT]):
    @property
    def initial_state(self) -> StateT: ...
    def common_state(self) -> dict: ...
    def decision_key(self, state: StateT) -> str: ...
    def make_query(self, state: StateT, query_id: str) -> ChoiceQuery: ...
    def apply(self, state: StateT, action_key: str) -> StateT: ...
    def is_terminal(self, state: StateT) -> bool: ...
    def terminal_outcome(self, state: StateT) -> dict: ...
    def state_payload(self, state: StateT): ...
```

See [`examples/custom_workflow.py`](examples/custom_workflow.py) for a complete runnable adapter and
[`docs/ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md) for the invariants that make a task safe to plug in.

Choose the runtime that matches your environment:

| Runtime | Use it when | Probability claim |
|---|---|---|
| `enumerate_probability_tree` | The complete finite tree is small | Complete joint leaf distribution |
| `build_probability_graph` | Equivalent states/cycles should be merged | Complete finite-horizon graph when fully expanded |
| `build_adaptive_probability_graph` | Calls need a hard query budget | Success/failure/**unresolved** mass over the explored graph |

## Runtime boundary

JevTree is currently designed for finite, deterministic tasks with enumerable legal actions and an
executable terminal verifier. It does not claim calibrated real-world success probabilities. In adaptive
mode, pruned branches remain explicit `unresolved` mass and are never presented as a complete leaf
distribution.

For webpages or tools, use a receding-horizon controller: observe the real state, build a small safe
prediction tree, choose one Pareto action, execute only that action, observe again, then replan. Put a
human or policy gate in front of irreversible actions.

## Repository layout

```text
src/jevtree/              runtime, adapters, provider, CLI, demo server
src/jevtree/web/          self-contained animated browser demo
examples/                 runnable integration example
docs/                     technical integration documentation
tests/                    unit and packaging tests
.github/workflows/ci.yml  Python 3.10–3.12 and Docker checks
Dockerfile                local/server demo image
```

## Security

- Keep `TYPESAFE_API_KEY` in the environment or an ignored `.env` file.
- The browser never receives the API key.
- Demo errors redact the configured key.
- Do not expose the demo directly to the public internet without authentication, rate limiting, and TLS.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
