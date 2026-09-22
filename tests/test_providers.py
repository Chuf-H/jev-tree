from __future__ import annotations

from jevtree.providers import HeuristicBackend, _normalized_probabilities
from jevtree.types import ChoiceQuery


def test_probability_normalization_clamps_and_normalizes() -> None:
    probabilities = _normalized_probabilities(("a", "b"), {"a": 3, "b": -1})
    assert probabilities == {"a": 1.0, "b": 0.0}


def test_offline_backend_is_available_for_smoke_tests() -> None:
    result = HeuristicBackend().choose_many(
        {"task": "smoke"},
        [
            ChoiceQuery(
                "q0",
                {"step": 0},
                "Prefer the smaller goal distance.",
                {"near": {"goal_distance": 0}, "far": {"goal_distance": 2}},
            )
        ],
    )
    assert result.choices[0].choice == "near"
    assert result.choices[0].probabilities["near"] > result.choices[0].probabilities["far"]
