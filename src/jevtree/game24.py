from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable

from .types import ChoiceQuery


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


@dataclass(frozen=True)
class Term:
    value: Fraction
    expression: str


@dataclass(frozen=True)
class ArithmeticAction:
    key: str
    left_index: int
    right_index: int
    operator: str
    result: Fraction


Game24State = tuple[Term, ...]


class Game24Problem:
    """Exact finite Game of 24 adapter with commutative symmetry removed."""

    def __init__(self, numbers: Iterable[int | str | Fraction], target: int = 24) -> None:
        terms = tuple(Term(Fraction(value), str(value)) for value in numbers)
        if len(terms) < 2:
            raise ValueError("Game of 24 requires at least two numbers")
        self.target = Fraction(target)
        self._initial_state = self._sort(terms)

    @staticmethod
    def _sort(terms: Iterable[Term]) -> Game24State:
        return tuple(sorted(terms, key=lambda term: (term.value, term.expression)))

    @property
    def initial_state(self) -> Game24State:
        return self._initial_state

    def common_state(self) -> dict[str, Any]:
        return {
            "task": "Game of 24",
            "target": _fraction_text(self.target),
            "rule": "Use every value once; each action combines two values with +, -, *, or exact rational /.",
        }

    def decision_key(self, state: Game24State) -> str:
        return ",".join(_fraction_text(term.value) for term in state)

    def _actions(self, state: Game24State) -> tuple[ArithmeticAction, ...]:
        actions: list[ArithmeticAction] = []
        key_counts: dict[str, int] = {}

        def add(left: int, right: int, operator: str, result: Fraction) -> None:
            left_text = _fraction_text(state[left].value)
            right_text = _fraction_text(state[right].value)
            base = f"{left_text}{operator}{right_text}"
            occurrence = key_counts.get(base, 0) + 1
            key_counts[base] = occurrence
            key = base if occurrence == 1 else f"{base}#{occurrence}"
            actions.append(ArithmeticAction(key, left, right, operator, result))

        for left in range(len(state)):
            for right in range(left + 1, len(state)):
                left_value = state[left].value
                right_value = state[right].value
                add(left, right, "+", left_value + right_value)
                add(left, right, "*", left_value * right_value)
                add(left, right, "-", left_value - right_value)
                add(right, left, "-", right_value - left_value)
                if right_value != 0:
                    add(left, right, "/", left_value / right_value)
                if left_value != 0:
                    add(right, left, "/", right_value / left_value)
        return tuple(actions)

    def _action_map(self, state: Game24State) -> dict[str, ArithmeticAction]:
        return {action.key: action for action in self._actions(state)}

    def make_query(self, state: Game24State, query_id: str) -> ChoiceQuery:
        criteria: dict[str, Any] = {}
        for action in self._actions(state):
            child = self.apply(state, action.key)
            criteria[action.key] = {
                "combine": [
                    _fraction_text(state[action.left_index].value),
                    action.operator,
                    _fraction_text(state[action.right_index].value),
                ],
                "next": [_fraction_text(term.value) for term in child],
                "goal_distance": float(min(abs(term.value - self.target) for term in child)),
            }
        return ChoiceQuery(
            query_id=query_id,
            state={
                "values": [_fraction_text(term.value) for term in state],
                "operations_left": len(state) - 1,
            },
            instruction="Choose the combine operation most likely to leave a path to exactly the target.",
            criteria=criteria,
        )

    def apply(self, state: Game24State, action_key: str) -> Game24State:
        action = self._action_map(state)[action_key]
        left = state[action.left_index]
        right = state[action.right_index]
        expression = f"({left.expression}{action.operator}{right.expression})"
        remaining = [term for index, term in enumerate(state) if index not in {action.left_index, action.right_index}]
        remaining.append(Term(action.result, expression))
        return self._sort(remaining)

    def is_terminal(self, state: Game24State) -> bool:
        return len(state) == 1

    def terminal_outcome(self, state: Game24State) -> dict[str, Any]:
        if not self.is_terminal(state):
            raise ValueError("terminal outcome requested for a non-terminal state")
        term = state[0]
        success = term.value == self.target
        return {
            "label": "success" if success else "failure",
            "terminal": True,
            "success": success,
            "value": _fraction_text(term.value),
            "expression": term.expression,
        }

    def state_payload(self, state: Game24State) -> Any:
        return [
            {"value": _fraction_text(term.value), "expression": term.expression}
            for term in state
        ]
