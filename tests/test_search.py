from __future__ import annotations

from jevtree.blocksworld import parse_problem
from jevtree.providers import HeuristicBackend
from jevtree.search import SearchConfig, search


SIMPLE = """
(define (problem move-b-on-a)
(:domain blocksworld-4ops)
(:objects a b)
(:init (handempty) (ontable a) (ontable b) (clear a) (clear b))
(:goal (and (on b a)))
)
"""


def test_search_solves_two_step_problem_and_records_probability() -> None:
    problem = parse_problem(SIMPLE)
    result = search(problem, HeuristicBackend(), SearchConfig(max_depth=4, beam_width=2, min_width=1))
    assert result.solved
    assert [action.pddl for action in result.plan] == ["(pick-up b)", "(stack b a)"]
    assert result.terminal_log_probability is not None
    assert result.layers
    assert result.edges


def test_greedy_is_beam_one() -> None:
    problem = parse_problem(SIMPLE)
    result = search(
        problem,
        HeuristicBackend(),
        SearchConfig(max_depth=4, beam_width=1, min_width=1, adaptive=False),
    )
    # The deterministic test scorer breaks the initial tie toward block a, so
    # greedy is intentionally allowed to fail while the wider graph succeeds.
    assert not result.solved
    assert all(layer.kept_nodes <= 1 for layer in result.layers)


def test_probability_ledger_accounts_for_retained_and_pruned_mass() -> None:
    problem = parse_problem(SIMPLE)
    result = search(problem, HeuristicBackend(), SearchConfig(max_depth=5, beam_width=2, min_width=1))
    assert result.layers
    for layer in result.layers:
        assert abs(layer.retained_mass + layer.pruned_mass - 1.0) < 1e-9
        assert layer.cycle_pruned_edges >= 0
    assert result.terminal_log_probability is not None
    assert result.terminal_log_probability <= 0.0
