from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, TypeVar

from .types import BatchChoiceResult, ChoiceQuery, DecisionBackend, Usage


StateT = TypeVar("StateT")


class FiniteDecisionProblem(Protocol[StateT]):
    """Adapter for a finite, deterministic decision process.

    ``decision_key`` may intentionally merge states that have the same legal
    decision semantics. The engine still retains every path as a separate leaf;
    it only reuses the model distribution for equivalent decision states.
    """

    @property
    def initial_state(self) -> StateT: ...

    def common_state(self) -> dict[str, Any]: ...

    def decision_key(self, state: StateT) -> str: ...

    def make_query(self, state: StateT, query_id: str) -> ChoiceQuery: ...

    def apply(self, state: StateT, action_key: str) -> StateT: ...

    def is_terminal(self, state: StateT) -> bool: ...

    def terminal_outcome(self, state: StateT) -> dict[str, Any]: ...

    def state_payload(self, state: StateT) -> Any: ...


@dataclass(frozen=True)
class ProbabilityPath:
    path_id: str
    state: Any = field(repr=False)
    actions: tuple[str, ...] = ()
    log_probability: float = 0.0


@dataclass(frozen=True)
class ProbabilityEdge:
    depth: int
    parent_path_id: str
    child_path_id: str
    decision_key: str
    action: str
    conditional_probability: float
    joint_probability: float


@dataclass(frozen=True)
class ProbabilityLeaf:
    path_id: str
    actions: tuple[str, ...]
    probability: float
    log_probability: float
    state: Any
    outcome: dict[str, Any]


@dataclass(frozen=True)
class ProbabilityLayer:
    depth: int
    active_paths: int
    unique_decision_states: int
    queried_states: int
    cache_hits: int
    generated_edges: int
    terminal_leaves: int
    provider_requests: int
    input_tokens: int
    output_tokens: int
    latency_ms: float


@dataclass(frozen=True)
class NextActionSummary:
    action: str
    joint_probability: float
    conditional_model_probability: float
    outcome_probability: dict[str, float]
    best_leaf_probability: float


@dataclass(frozen=True)
class ProbabilityTreeResult:
    leaves: tuple[ProbabilityLeaf, ...]
    edges: tuple[ProbabilityEdge, ...]
    layers: tuple[ProbabilityLayer, ...]
    usage: Usage
    model: str
    total_probability: float
    mass_error: float
    outcome_probability: dict[str, float]
    root_actions: tuple[NextActionSummary, ...]
    root_pareto_actions: tuple[str, ...]
    recommended_action: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _chunked(values: Sequence[ChoiceQuery], size: int) -> list[Sequence[ChoiceQuery]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _probability(log_probability: float) -> float:
    return 0.0 if log_probability == -math.inf else math.exp(log_probability)


def summarize_next_actions(
    leaves: Sequence[ProbabilityLeaf],
    *,
    prefix: Sequence[str] = (),
) -> tuple[NextActionSummary, ...]:
    """Condition a leaf distribution on ``prefix`` and aggregate next actions."""

    prefix_tuple = tuple(prefix)
    matching = [leaf for leaf in leaves if tuple(leaf.actions[: len(prefix_tuple)]) == prefix_tuple]
    prefix_mass = math.fsum(leaf.probability for leaf in matching)
    branches: dict[str, list[ProbabilityLeaf]] = {}
    for leaf in matching:
        if len(leaf.actions) > len(prefix_tuple):
            branches.setdefault(leaf.actions[len(prefix_tuple)], []).append(leaf)
    summaries: list[NextActionSummary] = []
    for action, branch_leaves in branches.items():
        branch_mass = math.fsum(leaf.probability for leaf in branch_leaves)
        outcomes: dict[str, float] = {}
        for leaf in branch_leaves:
            label = str(leaf.outcome.get("label", "unknown"))
            outcomes[label] = outcomes.get(label, 0.0) + leaf.probability
        conditional_outcomes = {
            label: mass / branch_mass if branch_mass else 0.0
            for label, mass in outcomes.items()
        }
        summaries.append(
            NextActionSummary(
                action=action,
                joint_probability=branch_mass,
                conditional_model_probability=branch_mass / prefix_mass if prefix_mass else 0.0,
                outcome_probability=conditional_outcomes,
                best_leaf_probability=max((leaf.probability for leaf in branch_leaves), default=0.0),
            )
        )
    return tuple(
        sorted(
            summaries,
            key=lambda item: (
                -item.outcome_probability.get("success", 0.0),
                -item.conditional_model_probability,
                item.action,
            ),
        )
    )


def pareto_next_actions(
    summaries: Sequence[NextActionSummary],
    *,
    preferred_outcome: str = "success",
) -> tuple[str, ...]:
    """Return actions not dominated on success mass and model probability."""

    frontier: list[str] = []
    for candidate in summaries:
        candidate_success = candidate.outcome_probability.get(preferred_outcome, 0.0)
        dominated = False
        for other in summaries:
            if other is candidate:
                continue
            other_success = other.outcome_probability.get(preferred_outcome, 0.0)
            if (
                other_success >= candidate_success
                and other.conditional_model_probability >= candidate.conditional_model_probability
                and (
                    other_success > candidate_success
                    or other.conditional_model_probability > candidate.conditional_model_probability
                )
            ):
                dominated = True
                break
        if not dominated:
            frontier.append(candidate.action)
    return tuple(frontier)


def derive_policy_path(
    leaves: Sequence[ProbabilityLeaf],
    *,
    mode: str = "downstream_success",
) -> tuple[str, ...]:
    """Derive one sequential policy from an already enumerated leaf distribution."""

    if mode not in {"downstream_success", "local_greedy"}:
        raise ValueError(f"unknown policy mode: {mode}")
    prefix: tuple[str, ...] = ()
    while True:
        summaries = summarize_next_actions(leaves, prefix=prefix)
        if not summaries:
            return prefix
        if mode == "downstream_success":
            selected = summaries[0]
        else:
            selected = max(
                summaries,
                key=lambda item: (item.conditional_model_probability, item.action),
            )
        prefix = (*prefix, selected.action)


def leaf_for_actions(
    leaves: Sequence[ProbabilityLeaf],
    actions: Sequence[str],
) -> ProbabilityLeaf:
    action_tuple = tuple(actions)
    matches = [leaf for leaf in leaves if tuple(leaf.actions) == action_tuple]
    if len(matches) != 1:
        raise ValueError(f"expected one leaf for action path, found {len(matches)}")
    return matches[0]


def enumerate_probability_tree(
    problem: FiniteDecisionProblem[StateT],
    backend: DecisionBackend,
    *,
    max_depth: int,
    question_batch_size: int = 128,
    exploration_epsilon: float = 0.0,
    include_edges: bool = True,
) -> ProbabilityTreeResult:
    """Enumerate every legal path through ``max_depth`` without probability pruning.

    Model calls are compressed by querying one representative for each
    ``decision_key`` and caching its complete action distribution. Logical paths
    remain separate, so the returned leaves form the actual path distribution.
    """

    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if question_batch_size < 1:
        raise ValueError("question_batch_size must be positive")
    if not 0.0 <= exploration_epsilon < 1.0:
        raise ValueError("exploration_epsilon must be in [0, 1)")

    frontier: list[ProbabilityPath] = [ProbabilityPath("p0", problem.initial_state)]
    leaves: list[ProbabilityLeaf] = []
    edges: list[ProbabilityEdge] = []
    layers: list[ProbabilityLayer] = []
    distribution_cache: dict[str, dict[str, float]] = {}
    action_keys_cache: dict[str, tuple[str, ...]] = {}
    usage = Usage()
    model = "local"
    next_path_number = 1
    logical_decision_paths = 0
    queried_decision_states = 0
    raw_zero_action_probabilities = 0

    for depth in range(max_depth + 1):
        active: list[ProbabilityPath] = []
        terminal_count = 0
        for path in frontier:
            if problem.is_terminal(path.state):
                terminal_count += 1
                leaves.append(
                    ProbabilityLeaf(
                        path_id=path.path_id,
                        actions=path.actions,
                        probability=_probability(path.log_probability),
                        log_probability=path.log_probability,
                        state=problem.state_payload(path.state),
                        outcome=problem.terminal_outcome(path.state),
                    )
                )
            else:
                active.append(path)

        if not active:
            if terminal_count:
                layers.append(
                    ProbabilityLayer(depth, 0, 0, 0, 0, 0, terminal_count, 0, 0, 0, 0.0)
                )
            frontier = []
            break

        if depth == max_depth:
            for path in active:
                leaves.append(
                    ProbabilityLeaf(
                        path_id=path.path_id,
                        actions=path.actions,
                        probability=_probability(path.log_probability),
                        log_probability=path.log_probability,
                        state=problem.state_payload(path.state),
                        outcome={"label": "unresolved", "terminal": False},
                    )
                )
            layers.append(
                ProbabilityLayer(depth, len(active), len({problem.decision_key(p.state) for p in active}), 0, 0, 0, terminal_count, 0, 0, 0, 0.0)
            )
            frontier = []
            break

        logical_decision_paths += len(active)
        representative: dict[str, StateT] = {}
        for path in active:
            representative.setdefault(problem.decision_key(path.state), path.state)

        pending_queries: list[ChoiceQuery] = []
        query_to_key: dict[str, str] = {}
        for index, (decision_key, state) in enumerate(sorted(representative.items())):
            query = problem.make_query(state, f"d{depth}_s{index}")
            action_keys = tuple(query.criteria)
            if not action_keys:
                raise ValueError(f"non-terminal state has no legal actions: {decision_key}")
            prior = action_keys_cache.get(decision_key)
            if prior is not None and prior != action_keys:
                raise ValueError(f"decision-key collision changes actions: {decision_key}")
            action_keys_cache[decision_key] = action_keys
            if decision_key not in distribution_cache:
                pending_queries.append(query)
                query_to_key[query.query_id] = decision_key

        layer_usage = Usage()
        for query_chunk in _chunked(pending_queries, question_batch_size):
            batch: BatchChoiceResult = backend.choose_many(problem.common_state(), query_chunk)
            model = batch.model
            usage = usage + batch.usage
            layer_usage = layer_usage + batch.usage
            for answer in batch.choices:
                decision_key = query_to_key[answer.query_id]
                expected = action_keys_cache[decision_key]
                probabilities = {key: max(0.0, float(answer.probabilities.get(key, 0.0))) for key in expected}
                total = math.fsum(probabilities.values())
                if total <= 0.0 or not math.isfinite(total):
                    uniform = 1.0 / len(expected)
                    probabilities = {key: uniform for key in expected}
                else:
                    probabilities = {key: value / total for key, value in probabilities.items()}
                raw_zero_action_probabilities += sum(value == 0.0 for value in probabilities.values())
                if exploration_epsilon:
                    uniform_mass = exploration_epsilon / len(expected)
                    probabilities = {
                        key: (1.0 - exploration_epsilon) * value + uniform_mass
                        for key, value in probabilities.items()
                    }
                distribution_cache[decision_key] = probabilities

        queried = len(pending_queries)
        queried_decision_states += queried
        next_frontier: list[ProbabilityPath] = []
        generated_edges = 0
        for path in active:
            decision_key = problem.decision_key(path.state)
            expected = action_keys_cache[decision_key]
            current_keys = tuple(problem.make_query(path.state, "verify").criteria)
            if current_keys != expected:
                raise ValueError(f"equivalent decision states disagree on legal actions: {decision_key}")
            probabilities = distribution_cache[decision_key]
            for action_key in expected:
                conditional = probabilities[action_key]
                child_log_probability = (
                    -math.inf
                    if conditional <= 0.0 or path.log_probability == -math.inf
                    else path.log_probability + math.log(conditional)
                )
                child_id = f"p{next_path_number}"
                next_path_number += 1
                child = ProbabilityPath(
                    path_id=child_id,
                    state=problem.apply(path.state, action_key),
                    actions=(*path.actions, action_key),
                    log_probability=child_log_probability,
                )
                next_frontier.append(child)
                generated_edges += 1
                if include_edges:
                    edges.append(
                        ProbabilityEdge(
                            depth=depth,
                            parent_path_id=path.path_id,
                            child_path_id=child_id,
                            decision_key=decision_key,
                            action=action_key,
                            conditional_probability=conditional,
                            joint_probability=_probability(child_log_probability),
                        )
                    )
        layers.append(
            ProbabilityLayer(
                depth=depth,
                active_paths=len(active),
                unique_decision_states=len(representative),
                queried_states=queried,
                cache_hits=len(active) - queried,
                generated_edges=generated_edges,
                terminal_leaves=terminal_count,
                provider_requests=layer_usage.requests,
                input_tokens=layer_usage.input_tokens,
                output_tokens=layer_usage.output_tokens,
                latency_ms=layer_usage.latency_ms,
            )
        )
        frontier = next_frontier

    total_probability = math.fsum(leaf.probability for leaf in leaves)
    outcome_probability: dict[str, float] = {}
    for leaf in leaves:
        label = str(leaf.outcome.get("label", "unknown"))
        outcome_probability[label] = outcome_probability.get(label, 0.0) + leaf.probability
    root_actions = summarize_next_actions(leaves)
    root_pareto_actions = pareto_next_actions(root_actions)
    recommended_action = root_actions[0].action if root_actions else None
    return ProbabilityTreeResult(
        leaves=tuple(leaves),
        edges=tuple(edges),
        layers=tuple(layers),
        usage=usage,
        model=model,
        total_probability=total_probability,
        mass_error=abs(1.0 - total_probability),
        outcome_probability=outcome_probability,
        root_actions=root_actions,
        root_pareto_actions=root_pareto_actions,
        recommended_action=recommended_action,
        metadata={
            "logical_decision_paths": logical_decision_paths,
            "queried_decision_states": queried_decision_states,
            "query_compression_ratio": (
                logical_decision_paths / queried_decision_states if queried_decision_states else None
            ),
            "exploration_epsilon": exploration_epsilon,
            "probability_semantics": "raw_jev" if exploration_epsilon == 0.0 else "jev_uniform_mixture",
            "raw_zero_action_probabilities": raw_zero_action_probabilities,
            "complete": not any(leaf.outcome.get("label") == "unresolved" for leaf in leaves),
        },
    )
