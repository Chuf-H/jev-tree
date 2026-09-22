from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .blocksworld import Action, BlocksworldProblem, State
from .types import ChoiceQuery, DecisionBackend, Usage


def _logaddexp(left: float, right: float) -> float:
    if left == -math.inf:
        return right
    if right == -math.inf:
        return left
    high, low = max(left, right), min(left, right)
    return high + math.log1p(math.exp(low - high))


@dataclass(frozen=True)
class SearchConfig:
    max_depth: int = 48
    beam_width: int = 8
    min_width: int = 2
    mass_coverage: float = 0.98
    heuristic_weight: float = 1.25
    probability_weight: float = 1.0
    adaptive: bool = True
    stop_on_first_goal: bool = True


@dataclass
class GraphNode:
    state: State
    depth: int
    log_mass: float
    best_log_probability: float
    best_path: tuple[Action, ...]
    heuristic: int
    parent_count: int = 1


@dataclass(frozen=True)
class LedgerEdge:
    depth: int
    parent_state: str
    action: str
    child_state: str
    conditional_probability: float
    parent_log_mass: float
    child_path_log_probability: float
    child_heuristic: int


@dataclass(frozen=True)
class LayerRecord:
    depth: int
    input_nodes: int
    expanded_nodes: int
    generated_edges: int
    merged_nodes: int
    kept_nodes: int
    pruned_nodes: int
    cycle_pruned_edges: int
    retained_mass: float
    pruned_mass: float
    backend_model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


@dataclass(frozen=True)
class SearchResult:
    solved: bool
    plan: tuple[Action, ...]
    final_state: State
    depth: int
    usage: Usage
    model: str
    layers: tuple[LayerRecord, ...]
    edges: tuple[LedgerEdge, ...]
    terminal_log_probability: float | None
    failure_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _state_key(state: State) -> str:
    return "|".join("(" + " ".join(atom) + ")" for atom in sorted(state))


def _query(problem: BlocksworldProblem, node: GraphNode, actions: tuple[Action, ...], index: int) -> ChoiceQuery:
    criteria: dict[str, Any] = {}
    for action in actions:
        child = problem.apply(node.state, action)
        criteria[action.key] = {
            "action": action.description,
            "resulting_state": problem.compact_state(child),
            "goal_distance": problem.goal_distance(child),
        }
    return ChoiceQuery(
        query_id=f"node_{node.depth}_{index}",
        state={
            "current": problem.compact_state(node.state),
            "goal": problem.compact_goal(),
            "steps_taken": node.depth,
        },
        instruction=(
            "Choose the legal next action most likely to lead to the complete goal. "
            "Avoid undoing useful goal relations unless required to free a blocked object."
        ),
        criteria=criteria,
    )


def _pareto_order(nodes: list[GraphNode], config: SearchConfig) -> list[GraphNode]:
    if not nodes:
        return []
    frontier: list[GraphNode] = []
    remainder: list[GraphNode] = []
    for candidate in nodes:
        dominated = any(
            other.heuristic <= candidate.heuristic
            and other.log_mass >= candidate.log_mass
            and (other.heuristic < candidate.heuristic or other.log_mass > candidate.log_mass)
            for other in nodes
            if other is not candidate
        )
        (remainder if dominated else frontier).append(candidate)

    def score(node: GraphNode) -> float:
        return config.heuristic_weight * node.heuristic - config.probability_weight * node.log_mass

    return sorted(frontier, key=score) + sorted(remainder, key=score)


def _select(nodes: list[GraphNode], config: SearchConfig) -> tuple[list[GraphNode], list[GraphNode]]:
    ordered = _pareto_order(nodes, config)
    if not ordered:
        return [], []
    if not config.adaptive:
        width = min(config.beam_width, len(ordered))
    else:
        max_log = max(node.log_mass for node in ordered)
        masses = [math.exp(node.log_mass - max_log) for node in ordered]
        total = sum(masses)
        cumulative = 0.0
        width = 0
        for mass in masses:
            cumulative += mass
            width += 1
            if width >= config.min_width and cumulative / total >= config.mass_coverage:
                break
        width = min(max(width, config.min_width), config.beam_width, len(ordered))
    return ordered[:width], ordered[width:]


def search(problem: BlocksworldProblem, backend: DecisionBackend, config: SearchConfig | None = None) -> SearchResult:
    config = config or SearchConfig()
    root = GraphNode(problem.initial, 0, 0.0, 0.0, (), problem.goal_distance(problem.initial))
    if problem.is_goal(root.state):
        return SearchResult(True, (), root.state, 0, Usage(), "local", (), (), 0.0)
    frontier = [root]
    seen_depth: dict[State, int] = {root.state: 0}
    usage = Usage()
    all_edges: list[LedgerEdge] = []
    layer_records: list[LayerRecord] = []
    model = "local"
    pruned_log_mass_total = -math.inf
    for depth in range(config.max_depth):
        query_nodes: list[GraphNode] = []
        queries: list[ChoiceQuery] = []
        actions_by_query: dict[str, tuple[Action, ...]] = {}
        deterministic: dict[int, tuple[Action, ...]] = {}
        for index, node in enumerate(frontier):
            actions = problem.legal_actions(node.state)
            if len(actions) == 1:
                deterministic[index] = actions
                continue
            query = _query(problem, node, actions, index)
            query_nodes.append(node)
            queries.append(query)
            actions_by_query[query.query_id] = actions
        common_state = {
            "domain": "Blocksworld with deterministic legal state transitions",
            "objects": list(problem.objects),
            "goal": problem.compact_goal(),
            "decision_rule": "Each Choice head is a different reachable state of the same planning problem.",
        }
        batch = backend.choose_many(common_state, queries)
        usage = usage + batch.usage
        model = batch.model
        distributions = {item.query_id: item.probabilities for item in batch.choices}
        next_by_state: dict[State, GraphNode] = {}
        generated_edges = 0
        cycle_pruned_edges = 0
        cycle_pruned_log_masses: list[float] = []
        for index, node in enumerate(frontier):
            actions = problem.legal_actions(node.state)
            if len(actions) == 1:
                probabilities = {actions[0].key: 1.0}
            else:
                query_id = f"node_{node.depth}_{index}"
                probabilities = distributions[query_id]
            for action in actions:
                probability = max(float(probabilities.get(action.key, 0.0)), 1e-12)
                child_state = problem.apply(node.state, action)
                path_log_probability = node.best_log_probability + math.log(probability)
                child_mass = node.log_mass + math.log(probability)
                child_path = (*node.best_path, action)
                heuristic = problem.goal_distance(child_state)
                generated_edges += 1
                all_edges.append(
                    LedgerEdge(
                        depth=depth,
                        parent_state=_state_key(node.state),
                        action=action.pddl,
                        child_state=_state_key(child_state),
                        conditional_probability=probability,
                        parent_log_mass=node.log_mass,
                        child_path_log_probability=path_log_probability,
                        child_heuristic=heuristic,
                    )
                )
                prior_depth = seen_depth.get(child_state)
                if prior_depth is not None and prior_depth < depth + 1:
                    cycle_pruned_edges += 1
                    cycle_pruned_log_masses.append(child_mass)
                    continue
                existing = next_by_state.get(child_state)
                if existing is None:
                    next_by_state[child_state] = GraphNode(
                        child_state,
                        depth + 1,
                        child_mass,
                        path_log_probability,
                        child_path,
                        heuristic,
                    )
                else:
                    existing.log_mass = _logaddexp(existing.log_mass, child_mass)
                    existing.parent_count += 1
                    if path_log_probability > existing.best_log_probability:
                        existing.best_log_probability = path_log_probability
                        existing.best_path = child_path
        candidates = list(next_by_state.values())
        for child_state in next_by_state:
            seen_depth[child_state] = min(seen_depth.get(child_state, depth + 1), depth + 1)
        goals = [node for node in candidates if problem.is_goal(node.state)]
        kept, pruned = _select([node for node in candidates if not problem.is_goal(node.state)], config)
        all_layer_log_masses = [node.log_mass for node in candidates] + cycle_pruned_log_masses
        layer_max = max(all_layer_log_masses, default=0.0)
        scaled_total = sum(math.exp(value - layer_max) for value in all_layer_log_masses)
        scaled_kept = sum(math.exp(node.log_mass - layer_max) for node in [*kept, *goals])
        retained_mass = scaled_kept / scaled_total if scaled_total else 0.0
        pruned_mass = 1.0 - retained_mass
        discarded_log_masses = [node.log_mass for node in pruned] + cycle_pruned_log_masses
        if discarded_log_masses:
            pruned_layer_log_mass = layer_max + math.log(sum(math.exp(value - layer_max) for value in discarded_log_masses))
            pruned_log_mass_total = _logaddexp(pruned_log_mass_total, pruned_layer_log_mass)
        layer_records.append(
            LayerRecord(
                depth=depth + 1,
                input_nodes=len(frontier),
                expanded_nodes=len(frontier),
                generated_edges=generated_edges,
                merged_nodes=len(candidates),
                kept_nodes=len(kept),
                pruned_nodes=len(pruned),
                cycle_pruned_edges=cycle_pruned_edges,
                retained_mass=retained_mass,
                pruned_mass=pruned_mass,
                backend_model=batch.model,
                input_tokens=batch.usage.input_tokens,
                output_tokens=batch.usage.output_tokens,
                latency_ms=batch.usage.latency_ms,
            )
        )
        if goals and config.stop_on_first_goal:
            best_goal = max(goals, key=lambda node: node.best_log_probability)
            return SearchResult(
                True,
                best_goal.best_path,
                best_goal.state,
                best_goal.depth,
                usage,
                model,
                tuple(layer_records),
                tuple(all_edges),
                best_goal.best_log_probability,
                metadata={"pruned_log_mass": pruned_log_mass_total},
            )
        frontier = [*goals, *kept] if not config.stop_on_first_goal else kept
        if not frontier:
            break
    best = min(frontier, key=lambda node: node.heuristic, default=root)
    return SearchResult(
        False,
        best.best_path,
        best.state,
        best.depth,
        usage,
        model,
        tuple(layer_records),
        tuple(all_edges),
        None,
        failure_reason="search_budget_exhausted" if frontier else "frontier_empty",
        metadata={"pruned_log_mass": pruned_log_mass_total},
    )
