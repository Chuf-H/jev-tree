from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Generic, Sequence, TypeVar

from .probability_tree import FiniteDecisionProblem, _chunked
from .types import BatchChoiceResult, ChoiceQuery, DecisionBackend, Usage


StateT = TypeVar("StateT")


@dataclass(frozen=True)
class GraphLayer:
    depth: int
    expanded_states: int
    discovered_states: int
    generated_edges: int
    provider_requests: int
    input_tokens: int
    output_tokens: int
    latency_ms: float


@dataclass(frozen=True)
class GraphActionSummary:
    action: str
    local_probability: float
    downstream_success_mass: float
    child_key: str
    downstream_failure_mass: float = 0.0
    downstream_unresolved_mass: float = 0.0
    pareto: bool = False


@dataclass(frozen=True)
class GraphOutcomeMass:
    success: float
    failure: float
    unresolved: float

    @property
    def total(self) -> float:
        return math.fsum((self.success, self.failure, self.unresolved))


@dataclass(frozen=True)
class GraphRolloutStep:
    step: int
    state: Any
    action: str
    local_probability: float
    downstream_success_mass: float
    pareto_actions: tuple[str, ...]


@dataclass(frozen=True)
class GraphRollout:
    mode: str
    success: bool
    outcome: dict[str, Any]
    actions: tuple[str, ...]
    joint_probability: float
    steps: tuple[GraphRolloutStep, ...]


@dataclass
class ProbabilityGraphResult(Generic[StateT]):
    problem: FiniteDecisionProblem[StateT] = field(repr=False)
    states: dict[str, StateT] = field(repr=False)
    transitions: dict[str, dict[str, str]] = field(repr=False)
    distributions: dict[str, dict[str, float]] = field(repr=False)
    outcomes: dict[str, dict[str, Any]] = field(repr=False)
    layers: tuple[GraphLayer, ...]
    usage: Usage
    model: str
    initial_key: str
    graph_depth: int
    complete: bool
    exploration_epsilon: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def state_count(self) -> int:
        return len(self.states)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self.transitions.values())

    @property
    def queried_state_count(self) -> int:
        return len(self.distributions)

    def _mass_function(self):
        @lru_cache(maxsize=None)
        def outcome_mass(key: str, remaining: int) -> GraphOutcomeMass:
            outcome = self.outcomes.get(key)
            if outcome is not None:
                if bool(outcome.get("success")):
                    return GraphOutcomeMass(1.0, 0.0, 0.0)
                return GraphOutcomeMass(0.0, 1.0, 0.0)
            if remaining <= 0 or key not in self.transitions or key not in self.distributions:
                return GraphOutcomeMass(0.0, 0.0, 1.0)
            children = [
                (
                    self.distributions[key][action],
                    outcome_mass(child, remaining - 1),
                )
                for action, child in self.transitions[key].items()
            ]
            return GraphOutcomeMass(
                success=math.fsum(probability * mass.success for probability, mass in children),
                failure=math.fsum(probability * mass.failure for probability, mass in children),
                unresolved=math.fsum(probability * mass.unresolved for probability, mass in children),
            )

        return outcome_mass

    def outcome_mass(self, state_key: str, remaining_steps: int) -> GraphOutcomeMass:
        """Return finite-horizon success, failure, and unexpanded probability mass."""

        return self._mass_function()(state_key, remaining_steps)

    def action_summaries(self, state_key: str, remaining_steps: int) -> tuple[GraphActionSummary, ...]:
        if state_key not in self.transitions or state_key not in self.distributions:
            return ()
        outcome_mass = self._mass_function()

        raw = [
            GraphActionSummary(
                action=action,
                local_probability=self.distributions[state_key][action],
                downstream_success_mass=outcome_mass(child, remaining_steps - 1).success,
                child_key=child,
                downstream_failure_mass=outcome_mass(child, remaining_steps - 1).failure,
                downstream_unresolved_mass=outcome_mass(child, remaining_steps - 1).unresolved,
            )
            for action, child in self.transitions[state_key].items()
        ]
        pareto_actions: set[str] = set()
        for candidate in raw:
            dominated = any(
                other is not candidate
                and other.local_probability >= candidate.local_probability
                and other.downstream_success_mass >= candidate.downstream_success_mass
                and other.downstream_unresolved_mass <= candidate.downstream_unresolved_mass
                and (
                    other.local_probability > candidate.local_probability
                    or other.downstream_success_mass > candidate.downstream_success_mass
                    or other.downstream_unresolved_mass < candidate.downstream_unresolved_mass
                )
                for other in raw
            )
            if not dominated:
                pareto_actions.add(candidate.action)
        return tuple(
            sorted(
                (
                    GraphActionSummary(
                        action=item.action,
                        local_probability=item.local_probability,
                        downstream_success_mass=item.downstream_success_mass,
                        child_key=item.child_key,
                        downstream_failure_mass=item.downstream_failure_mass,
                        downstream_unresolved_mass=item.downstream_unresolved_mass,
                        pareto=item.action in pareto_actions,
                    )
                    for item in raw
                ),
                key=lambda item: (
                    -item.downstream_success_mass,
                    item.downstream_unresolved_mass,
                    -item.local_probability,
                    item.action,
                ),
            )
        )

    def rollout(self, *, mode: str, max_steps: int, cycle_visit_limit: int | None = None) -> GraphRollout:
        if mode not in {"downstream_success", "local_greedy"}:
            raise ValueError(f"unknown rollout mode: {mode}")
        key = self.initial_key
        actions: list[str] = []
        steps: list[GraphRolloutStep] = []
        log_probability = 0.0
        visits = {key: 1}
        for step_number in range(1, max_steps + 1):
            if key in self.outcomes:
                break
            summaries = self.action_summaries(key, max_steps - step_number + 1)
            if not summaries:
                break
            if mode == "downstream_success":
                selected = summaries[0]
            else:
                selected = max(
                    summaries,
                    key=lambda item: (item.local_probability, item.action),
                )
            probability = selected.local_probability
            log_probability = -math.inf if probability <= 0 else log_probability + math.log(probability)
            steps.append(
                GraphRolloutStep(
                    step=step_number,
                    state=self.problem.state_payload(self.states[key]),
                    action=selected.action,
                    local_probability=probability,
                    downstream_success_mass=selected.downstream_success_mass,
                    pareto_actions=tuple(item.action for item in summaries if item.pareto),
                )
            )
            actions.append(selected.action)
            key = selected.child_key
            visits[key] = visits.get(key, 0) + 1
            if cycle_visit_limit is not None and visits[key] >= cycle_visit_limit:
                break
        outcome = self.outcomes.get(key, {"label": "unresolved", "success": False, "terminal": False})
        return GraphRollout(
            mode=mode,
            success=bool(outcome.get("success")),
            outcome=outcome,
            actions=tuple(actions),
            joint_probability=0.0 if log_probability == -math.inf else math.exp(log_probability),
            steps=tuple(steps),
        )


def build_probability_graph(
    problem: FiniteDecisionProblem[StateT],
    backend: DecisionBackend,
    *,
    max_graph_depth: int,
    max_states: int = 5000,
    question_batch_size: int = 128,
    exploration_epsilon: float = 0.0,
) -> ProbabilityGraphResult[StateT]:
    """Build a probability graph by merging equivalent physical states.

    Unlike complete path enumeration, this representation can contain cycles.
    Finite-horizon joint success mass is computed later by dynamic programming.
    """

    if max_graph_depth < 0:
        raise ValueError("max_graph_depth must be non-negative")
    if max_states < 1:
        raise ValueError("max_states must be positive")
    if not 0.0 <= exploration_epsilon < 1.0:
        raise ValueError("exploration_epsilon must be in [0, 1)")

    initial = problem.initial_state
    initial_key = problem.decision_key(initial)
    states: dict[str, StateT] = {initial_key: initial}
    transitions: dict[str, dict[str, str]] = {}
    distributions: dict[str, dict[str, float]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    layers: list[GraphLayer] = []
    usage = Usage()
    model = "local"
    frontier = [initial_key]
    expanded: set[str] = set()
    hit_state_limit = False

    queries: list[ChoiceQuery] = []
    query_to_key: dict[str, str] = {}
    for depth in range(max_graph_depth + 1):
        pending_keys = [key for key in frontier if key not in expanded]
        if not pending_keys:
            break
        expanded_this_layer = 0
        generated_edges = 0
        next_frontier: list[str] = []
        for index, key in enumerate(pending_keys):
            state = states[key]
            if problem.is_terminal(state):
                outcomes[key] = problem.terminal_outcome(state)
                expanded.add(key)
                continue
            if depth == max_graph_depth:
                continue
            query = problem.make_query(state, f"g{depth}_s{index}")
            if not query.criteria:
                raise ValueError(f"non-terminal state has no legal actions: {key}")
            queries.append(query)
            query_to_key[query.query_id] = key
            edges: dict[str, str] = {}
            for action in query.criteria:
                child = problem.apply(states[key], action)
                child_key = problem.decision_key(child)
                if child_key not in states:
                    if len(states) >= max_states:
                        hit_state_limit = True
                        continue
                    states[child_key] = child
                    next_frontier.append(child_key)
                edges[action] = child_key
                generated_edges += 1
            transitions[key] = edges
            expanded.add(key)
            expanded_this_layer += 1

        layers.append(
            GraphLayer(
                depth=depth,
                expanded_states=expanded_this_layer,
                discovered_states=len(states),
                generated_edges=generated_edges,
                provider_requests=0,
                input_tokens=0,
                output_tokens=0,
                latency_ms=0.0,
            )
        )
        frontier = list(dict.fromkeys(next_frontier))
        if hit_state_limit:
            break

    for query_chunk in _chunked(queries, question_batch_size):
        batch: BatchChoiceResult = backend.choose_many(problem.common_state(), query_chunk)
        model = batch.model
        usage = usage + batch.usage
        for answer in batch.choices:
            key = query_to_key[answer.query_id]
            action_keys = tuple(transitions[key])
            probabilities = {
                action: max(0.0, float(answer.probabilities.get(action, 0.0)))
                for action in action_keys
            }
            total = math.fsum(probabilities.values())
            if total <= 0.0 or not math.isfinite(total):
                probabilities = {action: 1.0 / len(action_keys) for action in action_keys}
            else:
                probabilities = {action: value / total for action, value in probabilities.items()}
            if exploration_epsilon:
                uniform_mass = exploration_epsilon / len(action_keys)
                probabilities = {
                    action: (1.0 - exploration_epsilon) * value + uniform_mass
                    for action, value in probabilities.items()
                }
            distributions[key] = probabilities
    if layers and usage.requests:
        layers[-1] = replace(
            layers[-1],
            provider_requests=usage.requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=usage.latency_ms,
        )

    for key, state in states.items():
        if problem.is_terminal(state):
            outcomes.setdefault(key, problem.terminal_outcome(state))
    expandable = {
        key
        for key, state in states.items()
        if not problem.is_terminal(state)
    }
    complete = not hit_state_limit and expandable.issubset(transitions)
    return ProbabilityGraphResult(
        problem=problem,
        states=states,
        transitions=transitions,
        distributions=distributions,
        outcomes=outcomes,
        layers=tuple(layers),
        usage=usage,
        model=model,
        initial_key=initial_key,
        graph_depth=max_graph_depth,
        complete=complete,
        exploration_epsilon=exploration_epsilon,
        metadata={
            "mode": "complete",
            "topology_complete": complete,
            "selected_query_states": len(distributions),
        },
    )


class _UniformTopologyBackend:
    """Provider-free backend used only to reuse generic topology discovery."""

    def choose_many(
        self,
        common_state: dict[str, Any],
        queries: Sequence[ChoiceQuery],
    ) -> BatchChoiceResult:
        del common_state
        choices = []
        for query in queries:
            probability = 1.0 / len(query.criteria)
            probabilities = {action: probability for action in query.criteria}
            first = next(iter(query.criteria))
            from .types import ChoiceDistribution

            choices.append(ChoiceDistribution(query.query_id, first, probabilities))
        return BatchChoiceResult(tuple(choices), "local-topology", Usage())


def _success_corridor_keys(
    topology: ProbabilityGraphResult[StateT],
    *,
    slack: int,
    max_query_states: int,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    if slack < 0:
        raise ValueError("corridor slack must be non-negative")
    if max_query_states < 1:
        raise ValueError("max_query_states must be positive")

    forward = {topology.initial_key: 0}
    queue = deque([topology.initial_key])
    while queue:
        key = queue.popleft()
        for child in topology.transitions.get(key, {}).values():
            if child not in forward:
                forward[child] = forward[key] + 1
                queue.append(child)

    reverse: dict[str, set[str]] = defaultdict(set)
    for parent, edges in topology.transitions.items():
        for child in edges.values():
            reverse[child].add(parent)
    success_keys = {
        key for key, outcome in topology.outcomes.items() if bool(outcome.get("success"))
    }
    to_success = {key: 0 for key in success_keys}
    queue = deque(success_keys)
    while queue:
        key = queue.popleft()
        for parent in reverse.get(key, ()):
            if parent not in to_success:
                to_success[parent] = to_success[key] + 1
                queue.append(parent)

    shortest = to_success.get(topology.initial_key)
    expandable = set(topology.transitions)
    if shortest is None:
        ordered = sorted(expandable, key=lambda key: (forward.get(key, math.inf), key))
        selected = tuple(ordered[:max_query_states])
        return selected, {
            "selection": "forward_fallback_no_discovered_success",
            "shortest_success_steps": None,
            "corridor_slack": slack,
            "candidate_states": len(ordered),
            "truncated_by_query_budget": len(ordered) > len(selected),
        }

    candidates = {
        key
        for key in expandable
        if key in forward
        and key in to_success
        and forward[key] + to_success[key] <= shortest + slack
    }

    # Protect one complete shortest path before filling the remaining budget.
    protected = [topology.initial_key]
    cursor = topology.initial_key
    while cursor not in success_keys:
        distance = to_success[cursor]
        next_keys = sorted(
            {
                child
                for child in topology.transitions.get(cursor, {}).values()
                if to_success.get(child) == distance - 1
            }
        )
        if not next_keys:
            break
        cursor = next_keys[0]
        if cursor in expandable:
            protected.append(cursor)
    if len(protected) > max_query_states:
        raise ValueError("query budget is smaller than one shortest success path")

    ranked = sorted(
        candidates - set(protected),
        key=lambda key: (
            forward[key] + to_success[key],
            forward[key],
            to_success[key],
            key,
        ),
    )
    selected = tuple([*protected, *ranked[: max_query_states - len(protected)]])
    return selected, {
        "selection": "verifier_success_corridor",
        "shortest_success_steps": shortest,
        "corridor_slack": slack,
        "candidate_states": len(candidates),
        "protected_shortest_path_states": len(protected),
        "truncated_by_query_budget": len(candidates) > len(selected),
    }


def build_sparse_probability_graph(
    problem: FiniteDecisionProblem[StateT],
    backend: DecisionBackend,
    *,
    max_graph_depth: int,
    max_states: int = 5000,
    max_query_states: int = 200,
    corridor_slack: int = 4,
    question_batch_size: int = 128,
    exploration_epsilon: float = 0.0,
) -> ProbabilityGraphResult[StateT]:
    """Build a generic verifier corridor and query Jev only inside it.

    Topology discovery uses only the public ``FiniteDecisionProblem`` interface.
    Probability mass that exits the queried corridor is reported as unresolved,
    never silently renormalized away.
    """

    topology = build_probability_graph(
        problem,
        _UniformTopologyBackend(),
        max_graph_depth=max_graph_depth,
        max_states=max_states,
        question_batch_size=max(1, question_batch_size),
        exploration_epsilon=0.0,
    )
    selected, selection_metadata = _success_corridor_keys(
        topology,
        slack=corridor_slack,
        max_query_states=max_query_states,
    )
    distributions: dict[str, dict[str, float]] = {}
    usage = Usage()
    model = "local"
    queries: list[ChoiceQuery] = []
    query_to_key: dict[str, str] = {}
    for index, key in enumerate(selected):
        query = problem.make_query(topology.states[key], f"sparse_s{index}")
        queries.append(query)
        query_to_key[query.query_id] = key

    for query_chunk in _chunked(queries, question_batch_size):
        batch = backend.choose_many(problem.common_state(), query_chunk)
        model = batch.model
        usage = usage + batch.usage
        for answer in batch.choices:
            key = query_to_key[answer.query_id]
            action_keys = tuple(topology.transitions[key])
            probabilities = {
                action: max(0.0, float(answer.probabilities.get(action, 0.0)))
                for action in action_keys
            }
            total = math.fsum(probabilities.values())
            if total <= 0.0 or not math.isfinite(total):
                probabilities = {action: 1.0 / len(action_keys) for action in action_keys}
            else:
                probabilities = {action: value / total for action, value in probabilities.items()}
            if exploration_epsilon:
                uniform_mass = exploration_epsilon / len(action_keys)
                probabilities = {
                    action: (1.0 - exploration_epsilon) * value + uniform_mass
                    for action, value in probabilities.items()
                }
            distributions[key] = probabilities

    expandable = {
        key for key, state in topology.states.items() if not problem.is_terminal(state)
    }
    probability_complete = expandable.issubset(distributions)
    metadata = {
        "mode": "sparse_verifier_corridor",
        "topology_complete": topology.complete,
        "topology_states": topology.state_count,
        "topology_edges": topology.edge_count,
        "selected_query_states": len(distributions),
        "probability_complete": probability_complete,
        **selection_metadata,
    }
    return ProbabilityGraphResult(
        problem=problem,
        states=topology.states,
        transitions=topology.transitions,
        distributions=distributions,
        outcomes=topology.outcomes,
        layers=topology.layers,
        usage=usage,
        model=model,
        initial_key=topology.initial_key,
        graph_depth=max_graph_depth,
        complete=probability_complete,
        exploration_epsilon=exploration_epsilon,
        metadata=metadata,
    )


def build_adaptive_probability_graph(
    problem: FiniteDecisionProblem[StateT],
    backend: DecisionBackend,
    *,
    max_graph_depth: int,
    max_states: int = 5000,
    max_query_states: int = 200,
    expansion_batch_size: int = 32,
    post_success_queries: int = 32,
    question_batch_size: int = 128,
    exploration_epsilon: float = 0.0,
) -> ProbabilityGraphResult[StateT]:
    """Expand a Jev-prioritized graph without a reverse-success oracle.

    Each unqueried state is prioritized by the largest joint probability of its
    best discovered path from the root. A terminal verifier is called only after
    a forward transition discovers that terminal. Once the first success is found,
    a bounded number of additional states are queried to expose alternatives.

    Unqueried frontier branches remain explicit unresolved probability mass. The
    routine never enumerates topology in advance and never computes distance from
    a successful terminal back to candidate states.
    """

    if max_graph_depth < 0:
        raise ValueError("max_graph_depth must be non-negative")
    if max_states < 1:
        raise ValueError("max_states must be positive")
    if max_query_states < 1:
        raise ValueError("max_query_states must be positive")
    if expansion_batch_size < 1:
        raise ValueError("expansion_batch_size must be positive")
    if post_success_queries < 0:
        raise ValueError("post_success_queries must be non-negative")
    if question_batch_size < 1:
        raise ValueError("question_batch_size must be positive")
    if not 0.0 <= exploration_epsilon < 1.0:
        raise ValueError("exploration_epsilon must be in [0, 1)")

    initial = problem.initial_state
    initial_key = problem.decision_key(initial)
    states: dict[str, StateT] = {initial_key: initial}
    transitions: dict[str, dict[str, str]] = {}
    distributions: dict[str, dict[str, float]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    layers: list[GraphLayer] = []
    usage = Usage()
    model = "local"

    if problem.is_terminal(initial):
        outcomes[initial_key] = problem.terminal_outcome(initial)

    # Values are (best log joint probability, shallowest depth at that score).
    best_path: dict[str, tuple[float, int]] = {initial_key: (0.0, 0)}
    frontier: set[str] = set() if initial_key in outcomes else {initial_key}
    first_success_after_queries: int | None = None
    wave = 0

    def relax_from(start_key: str) -> None:
        """Propagate an improved path score through already queried states."""

        queue = deque([start_key])
        while queue:
            parent = queue.popleft()
            if parent not in distributions or parent not in transitions:
                continue
            parent_log, parent_depth = best_path[parent]
            if parent_depth >= max_graph_depth:
                continue
            for action, child in transitions[parent].items():
                probability = distributions[parent][action]
                if probability <= 0.0:
                    continue
                candidate = (parent_log + math.log(probability), parent_depth + 1)
                previous = best_path.get(child)
                improves = previous is None or candidate[0] > previous[0] + 1e-15
                ties_shorter = (
                    previous is not None
                    and abs(candidate[0] - previous[0]) <= 1e-15
                    and candidate[1] < previous[1]
                )
                if not improves and not ties_shorter:
                    continue
                best_path[child] = candidate
                if child in outcomes:
                    continue
                if child in distributions:
                    queue.append(child)
                else:
                    frontier.add(child)

    while frontier and len(distributions) < max_query_states:
        if first_success_after_queries is not None:
            stop_after = min(max_query_states, first_success_after_queries + post_success_queries)
            if len(distributions) >= stop_after:
                break

        remaining_budget = max_query_states - len(distributions)
        if first_success_after_queries is not None:
            remaining_budget = min(
                remaining_budget,
                first_success_after_queries + post_success_queries - len(distributions),
            )
        selected = sorted(
            frontier,
            key=lambda key: (
                -best_path[key][0],
                best_path[key][1],
                key,
            ),
        )[: min(expansion_batch_size, remaining_budget)]
        if not selected:
            break
        for key in selected:
            frontier.discard(key)

        queries: list[ChoiceQuery] = []
        query_to_key: dict[str, str] = {}
        for index, key in enumerate(selected):
            state = states[key]
            if problem.is_terminal(state):
                outcomes[key] = problem.terminal_outcome(state)
                continue
            if best_path[key][1] >= max_graph_depth:
                continue
            query = problem.make_query(state, f"adaptive_w{wave}_s{index}")
            if not query.criteria:
                raise ValueError(f"non-terminal state has no legal actions: {key}")
            queries.append(query)
            query_to_key[query.query_id] = key

        wave_usage = Usage()
        answers_by_id = {}
        for query_chunk in _chunked(queries, question_batch_size):
            batch = backend.choose_many(problem.common_state(), query_chunk)
            model = batch.model
            usage = usage + batch.usage
            wave_usage = wave_usage + batch.usage
            answers_by_id.update({answer.query_id: answer for answer in batch.choices})

        generated_edges = 0
        expanded_states = 0
        wave_found_success = False
        for query in queries:
            key = query_to_key[query.query_id]
            answer = answers_by_id[query.query_id]
            action_keys = tuple(query.criteria)
            probabilities = {
                action: max(0.0, float(answer.probabilities.get(action, 0.0)))
                for action in action_keys
            }
            total = math.fsum(probabilities.values())
            if total <= 0.0 or not math.isfinite(total):
                probabilities = {action: 1.0 / len(action_keys) for action in action_keys}
            else:
                probabilities = {action: value / total for action, value in probabilities.items()}
            if exploration_epsilon:
                uniform_mass = exploration_epsilon / len(action_keys)
                probabilities = {
                    action: (1.0 - exploration_epsilon) * value + uniform_mass
                    for action, value in probabilities.items()
                }
            distributions[key] = probabilities

            edges: dict[str, str] = {}
            for action in action_keys:
                child = problem.apply(states[key], action)
                child_key = problem.decision_key(child)
                if child_key not in states:
                    if len(states) >= max_states:
                        raise ValueError("adaptive graph exceeded max_states")
                    states[child_key] = child
                edges[action] = child_key
                generated_edges += 1
                if problem.is_terminal(child):
                    outcome = problem.terminal_outcome(child)
                    outcomes[child_key] = outcome
                    if bool(outcome.get("success")):
                        wave_found_success = True
            transitions[key] = edges
            expanded_states += 1
            relax_from(key)

        if wave_found_success and first_success_after_queries is None:
            # Batch calls are indivisible: all selected queries have been incurred.
            first_success_after_queries = len(distributions)

        layers.append(
            GraphLayer(
                depth=wave,
                expanded_states=expanded_states,
                discovered_states=len(states),
                generated_edges=generated_edges,
                provider_requests=wave_usage.requests,
                input_tokens=wave_usage.input_tokens,
                output_tokens=wave_usage.output_tokens,
                latency_ms=wave_usage.latency_ms,
            )
        )
        wave += 1

    expandable = {
        key for key, state in states.items() if not problem.is_terminal(state)
    }
    probability_complete = expandable.issubset(distributions)
    success_terminals = sum(bool(outcome.get("success")) for outcome in outcomes.values())
    metadata = {
        "mode": "adaptive_probability_frontier",
        "selection": "max_discovered_joint_path_probability",
        "reverse_success_oracle": False,
        "topology_complete": probability_complete,
        "topology_states": len(states),
        "topology_edges": sum(len(edges) for edges in transitions.values()),
        "selected_query_states": len(distributions),
        "probability_complete": probability_complete,
        "frontier_states_remaining": len(frontier),
        "success_terminals_discovered": success_terminals,
        "first_success_after_queries": first_success_after_queries,
        "post_success_queries": post_success_queries,
        "expansion_batch_size": expansion_batch_size,
        "query_budget_exhausted": len(distributions) >= max_query_states,
    }
    return ProbabilityGraphResult(
        problem=problem,
        states=states,
        transitions=transitions,
        distributions=distributions,
        outcomes=outcomes,
        layers=tuple(layers),
        usage=usage,
        model=model,
        initial_key=initial_key,
        graph_depth=max_graph_depth,
        complete=probability_complete,
        exploration_epsilon=exploration_epsilon,
        metadata=metadata,
    )
