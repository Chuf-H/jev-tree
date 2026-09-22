# Adapter guide

JevTree keeps domain logic outside the planner. An adapter supplies eight deterministic operations; the
runtime supplies batching, caching, probability composition, state merging, mass accounting, Pareto
summaries, and replay.

## Contract

1. `initial_state` returns an immutable or safely reusable state.
2. `common_state()` contains task context shared by every Jev question.
3. `decision_key(state)` is stable and equal only when two states have the same decision semantics.
4. `make_query(...)` returns every legal action exactly once in `criteria`.
5. `apply(state, action)` is deterministic and must not mutate `state`.
6. `is_terminal(state)` is cheap and side-effect free.
7. `terminal_outcome(state)` returns at least `label`, `terminal`, and `success`.
8. `state_payload(state)` returns a JSON-serializable diagnostic view.

## Safety invariants

- Never place a gold answer or reverse distance-to-answer oracle in `make_query`.
- Verify a terminal outcome only after the forward transition reaches it.
- Keep illegal actions out of `criteria`; do not ask the model to enforce hard constraints.
- Use `decision_key` conservatively. Merging non-equivalent states corrupts the probability graph.
- In partial or stochastic environments, re-observe after every real action and replan.
- Mark unexpanded branches as unresolved; never renormalize them away.
- Put policy/human approval in front of irreversible actions.

## Choosing a builder

### Exact tree

Use `enumerate_probability_tree` when all legal paths fit comfortably in memory. Equivalent decision
states reuse one Jev distribution, while every logical path remains a separate probability leaf.

### Complete merged graph

Use `build_probability_graph` when multiple paths reach the same state or when finite-horizon cycles are
possible. Downstream mass is computed with finite-horizon dynamic programming.

### Adaptive graph

Use `build_adaptive_probability_graph` when provider calls need a hard cap. Expansion follows the highest
known joint path mass. The result exposes success, failure, and unresolved mass separately.

## Production controller

For a live browser, API, robot, or game, do not execute a whole predicted plan blindly:

```text
observe real state
  -> enumerate safe candidate actions
  -> build a short prediction graph
  -> batch Jev judgments
  -> select a Pareto action
  -> risk gate
  -> execute one action
  -> observe and replan
```

Cache only against a stable observation/version key, and discard old subtrees when the real transition does
not match the predicted child state.
