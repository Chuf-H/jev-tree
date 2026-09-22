from __future__ import annotations

import inspect

import pytest

from jevtree.game24 import Game24Problem
from jevtree.probability_graph import (
    build_adaptive_probability_graph,
    build_probability_graph,
    build_sparse_probability_graph,
)
from jevtree.types import BatchChoiceResult, ChoiceDistribution, ChoiceQuery, Usage


class UniformBackend:
    def choose_many(self, common_state: dict, queries: list[ChoiceQuery]) -> BatchChoiceResult:
        choices = []
        for query in queries:
            probability = 1.0 / len(query.criteria)
            probabilities = {action: probability for action in query.criteria}
            choices.append(ChoiceDistribution(query.query_id, next(iter(query.criteria)), probabilities))
        return BatchChoiceResult(tuple(choices), "uniform-graph-test", Usage(requests=1))


def test_adaptive_builder_has_no_reverse_success_corridor_dependency() -> None:
    source = inspect.getsource(build_adaptive_probability_graph)

    assert "_success_corridor_keys" not in source
    assert "to_success" not in source
    assert '"reverse_success_oracle": False' in source


def test_minigrid_probability_graph_finds_multistep_solution() -> None:
    pytest.importorskip("minigrid")
    from jevtree.minigrid_game import MiniGridGameProblem

    problem = MiniGridGameProblem(seed=0)
    result = build_probability_graph(
        problem,
        UniformBackend(),
        max_graph_depth=40,
        max_states=3000,
        question_batch_size=128,
    )

    tree = result.rollout(mode="downstream_success", max_steps=40)
    greedy = result.rollout(mode="local_greedy", max_steps=40)

    assert result.complete is True
    assert result.state_count < 3000
    assert result.edge_count > result.state_count
    assert tree.success is True
    assert greedy.success is False
    assert tree.steps[0].pareto_actions
    assert tree.steps[-1].downstream_success_mass == 1.0


def test_sparse_graph_conserves_three_way_mass_on_game24() -> None:
    problem = Game24Problem([2, 5, 6, 6])
    result = build_sparse_probability_graph(
        problem,
        UniformBackend(),
        max_graph_depth=3,
        max_query_states=200,
        corridor_slack=4,
        question_batch_size=128,
    )

    mass = result.outcome_mass(result.initial_key, 3)
    rollout = result.rollout(mode="downstream_success", max_steps=3)

    assert result.metadata["mode"] == "sparse_verifier_corridor"
    assert result.metadata["topology_complete"] is True
    assert result.queried_state_count <= 200
    assert abs(mass.total - 1.0) <= 1e-9
    assert mass.success > 0.0
    assert mass.unresolved > 0.0
    assert rollout.success is True


def test_sparse_graph_is_domain_agnostic_and_solves_minigrid() -> None:
    pytest.importorskip("minigrid")
    from jevtree.minigrid_game import MiniGridGameProblem

    problem = MiniGridGameProblem("MiniGrid-DoorKey-6x6-v0", seed=2)
    result = build_sparse_probability_graph(
        problem,
        UniformBackend(),
        max_graph_depth=60,
        max_query_states=200,
        corridor_slack=4,
        question_batch_size=128,
    )

    mass = result.outcome_mass(result.initial_key, 60)
    rollout = result.rollout(mode="downstream_success", max_steps=60)

    assert result.metadata["candidate_states"] == 123
    assert result.queried_state_count == 123
    assert abs(mass.total - 1.0) <= 1e-9
    assert mass.success > 0.0
    assert mass.unresolved > 0.0
    assert rollout.success is True


def test_adaptive_graph_finds_game24_without_reverse_success_oracle() -> None:
    problem = Game24Problem([1, 2, 3, 4])
    result = build_adaptive_probability_graph(
        problem,
        UniformBackend(),
        max_graph_depth=3,
        max_query_states=200,
        expansion_batch_size=32,
        post_success_queries=32,
        question_batch_size=128,
        exploration_epsilon=0.01,
    )

    mass = result.outcome_mass(result.initial_key, 3)
    rollout = result.rollout(mode="downstream_success", max_steps=3)

    assert result.metadata["mode"] == "adaptive_probability_frontier"
    assert result.metadata["reverse_success_oracle"] is False
    assert result.metadata["success_terminals_discovered"] > 0
    assert result.queried_state_count <= 200
    assert abs(mass.total - 1.0) <= 1e-9
    assert mass.success > 0.0
    assert mass.unresolved > 0.0
    assert rollout.success is True


def test_adaptive_graph_keeps_minigrid_budget_and_unresolved_mass_without_oracle() -> None:
    pytest.importorskip("minigrid")
    from jevtree.minigrid_game import MiniGridGameProblem

    problem = MiniGridGameProblem("MiniGrid-DoorKey-6x6-v0", seed=0)
    result = build_adaptive_probability_graph(
        problem,
        UniformBackend(),
        max_graph_depth=60,
        max_query_states=200,
        expansion_batch_size=32,
        post_success_queries=32,
        question_batch_size=128,
        exploration_epsilon=0.01,
    )

    mass = result.outcome_mass(result.initial_key, 60)
    rollout = result.rollout(mode="downstream_success", max_steps=60)

    assert result.metadata["reverse_success_oracle"] is False
    assert result.state_count < 1294
    assert result.queried_state_count <= 200
    assert abs(mass.total - 1.0) <= 1e-9
    # A uniform backend is intentionally not rescued by a reverse-success oracle.
    # Success quality is covered by the native-Jev development evaluation.
    assert mass.unresolved > 0.0
    assert rollout.outcome["label"] in {"success", "unresolved"}
