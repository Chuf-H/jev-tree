from __future__ import annotations

import pytest

from jevtree.blocksworld import Action, parse_problem


SAMPLE = """
(define (problem BW-rand-6)
(:domain blocksworld-4ops)
(:objects a b c d e f )
(:init
  (handempty)
  (ontable a)
  (on b f)
  (on c a)
  (on d b)
  (on e c)
  (on f e)
  (clear d))
(:goal (and (on a f) (on b c) (on c a) (on d b) (on f e)))
)
"""


def test_parse_problem_and_first_legal_action() -> None:
    problem = parse_problem(SAMPLE)
    assert problem.name == "bw-rand-6"
    assert problem.objects == ("a", "b", "c", "d", "e", "f")
    assert problem.legal_actions(problem.initial) == (Action("unstack", ("d", "b")),)


def test_apply_rejects_illegal_action() -> None:
    problem = parse_problem(SAMPLE)
    with pytest.raises(ValueError, match="illegal action"):
        problem.apply(problem.initial, Action("pick-up", ("a",)))


def test_action_parse_and_pddl_roundtrip() -> None:
    action = Action.parse("(stack d c)")
    assert action == Action("stack", ("d", "c"))
    assert action.pddl == "(stack d c)"


def test_goal_distance_requires_the_whole_support_chain_to_be_stable() -> None:
    problem = parse_problem(
        """
        (define (problem tower)
        (:domain blocksworld-4ops)
        (:objects a b c)
        (:init (handempty) (ontable a) (ontable b) (on c b) (clear a) (clear c))
        (:goal (and (on b a) (on c b))))
        """
    )
    # Although (on c b) is locally true, b must move, so c is not settled.
    assert problem.goal_distance(problem.initial) >= 4
