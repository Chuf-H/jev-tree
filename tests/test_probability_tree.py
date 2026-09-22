from __future__ import annotations

from typing import Sequence

from jevtree.game24 import Game24Problem
from jevtree.probability_tree import (
    derive_policy_path,
    enumerate_probability_tree,
    leaf_for_actions,
    summarize_next_actions,
)
from jevtree.types import BatchChoiceResult, ChoiceDistribution, ChoiceQuery, Usage


class UniformBackend:
    def __init__(self) -> None:
        self.queries = 0
        self.requests = 0

    def choose_many(self, common_state: dict, queries: Sequence[ChoiceQuery]) -> BatchChoiceResult:
        self.requests += 1
        self.queries += len(queries)
        answers = []
        for query in queries:
            probability = 1.0 / len(query.criteria)
            probabilities = {key: probability for key in query.criteria}
            answers.append(ChoiceDistribution(query.query_id, next(iter(query.criteria)), probabilities))
        return BatchChoiceResult(tuple(answers), "uniform-test", Usage(requests=1))


class SparseBackend(UniformBackend):
    def choose_many(self, common_state: dict, queries: Sequence[ChoiceQuery]) -> BatchChoiceResult:
        self.requests += 1
        self.queries += len(queries)
        answers = []
        for query in queries:
            first = next(iter(query.criteria))
            probabilities = {key: float(key == first) for key in query.criteria}
            answers.append(ChoiceDistribution(query.query_id, first, probabilities))
        return BatchChoiceResult(tuple(answers), "sparse-test", Usage(requests=1))


def _independent_leaf_count(problem: Game24Problem, state, depth: int) -> int:
    if problem.is_terminal(state):
        return 1
    if depth == 0:
        return 1
    query = problem.make_query(state, "count")
    return sum(_independent_leaf_count(problem, problem.apply(state, action), depth - 1) for action in query.criteria)


def test_game24_enumerates_every_canonical_leaf_and_conserves_mass() -> None:
    problem = Game24Problem([1, 2, 3, 4])
    backend = UniformBackend()
    result = enumerate_probability_tree(problem, backend, max_depth=3, question_batch_size=64)

    expected_leaves = _independent_leaf_count(problem, problem.initial_state, 3)
    assert len(result.leaves) == expected_leaves
    assert len({leaf.actions for leaf in result.leaves}) == expected_leaves
    assert result.mass_error <= 1e-9
    assert result.metadata["complete"] is True
    assert result.outcome_probability["success"] > 0.0
    assert any(
        leaf.outcome.get("expression") in {"(((1+2)+3)*4)", "(4*((1+2)+3))"}
        for leaf in result.leaves
    )
    assert result.recommended_action is not None
    assert result.recommended_action in {summary.action for summary in result.root_actions}
    assert result.root_pareto_actions
    assert abs(sum(summary.conditional_model_probability for summary in result.root_actions) - 1.0) <= 1e-9

    next_step = summarize_next_actions(result.leaves, prefix=[result.recommended_action])
    assert next_step
    assert abs(sum(summary.conditional_model_probability for summary in next_step) - 1.0) <= 1e-9

    downstream_path = derive_policy_path(result.leaves, mode="downstream_success")
    greedy_path = derive_policy_path(result.leaves, mode="local_greedy")
    assert len(downstream_path) == 3
    assert len(greedy_path) == 3
    assert leaf_for_actions(result.leaves, downstream_path).outcome["terminal"] is True
    assert leaf_for_actions(result.leaves, greedy_path).outcome["terminal"] is True


def test_unique_state_queries_compress_logical_tree_paths() -> None:
    problem = Game24Problem([1, 2, 3, 4])
    backend = UniformBackend()
    result = enumerate_probability_tree(problem, backend, max_depth=3, question_batch_size=32, include_edges=False)

    assert backend.queries == result.metadata["queried_decision_states"]
    assert result.metadata["logical_decision_paths"] > backend.queries
    assert result.metadata["query_compression_ratio"] > 2.0
    assert result.usage.requests == backend.requests


def test_exploration_mixture_gives_every_leaf_positive_mass() -> None:
    problem = Game24Problem([1, 2, 3, 4])
    result = enumerate_probability_tree(
        problem,
        SparseBackend(),
        max_depth=3,
        exploration_epsilon=0.01,
        include_edges=False,
    )

    assert result.metadata["probability_semantics"] == "jev_uniform_mixture"
    assert result.metadata["raw_zero_action_probabilities"] > 0
    assert min(leaf.probability for leaf in result.leaves) > 0.0
    assert result.mass_error <= 1e-9
