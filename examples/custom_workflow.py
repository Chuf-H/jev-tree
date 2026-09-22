"""Minimal custom JevTree adapter.

Run offline:
    python examples/custom_workflow.py

Run with Jev:
    TYPESAFE_API_KEY=... python examples/custom_workflow.py --live
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from jevtree import (
    ChoiceQuery,
    HeuristicBackend,
    TypeSafeBackend,
    derive_policy_path,
    enumerate_probability_tree,
)


@dataclass(frozen=True)
class WorkflowState:
    step: int = 0
    risk: int = 0
    minutes: int = 0
    actions: tuple[str, ...] = ()


class ReleaseWorkflow:
    """Three-step release workflow with an executable terminal verifier."""

    stages = (
        {"fresh_build": (0, 12), "reuse_cache": (2, 3)},
        {"full_suite": (0, 18), "smoke_only": (3, 4)},
        {"canary": (0, 8), "direct_prod": (4, 1)},
    )

    @property
    def initial_state(self) -> WorkflowState:
        return WorkflowState()

    def common_state(self) -> dict[str, Any]:
        return {
            "task": "Choose a reliable release path",
            "objective": "Keep total risk at or below 2 while avoiding unnecessary time.",
        }

    def decision_key(self, state: WorkflowState) -> str:
        return f"{state.step}:{state.risk}:{state.minutes}"

    def make_query(self, state: WorkflowState, query_id: str) -> ChoiceQuery:
        criteria = {}
        for action, (risk_delta, minute_delta) in self.stages[state.step].items():
            next_risk = state.risk + risk_delta
            criteria[action] = {
                "next_risk": next_risk,
                "next_minutes": state.minutes + minute_delta,
                "risk_limit": 2,
                # Used only by the deterministic offline smoke backend.
                "goal_distance": max(0, next_risk - 2) + 0.01 * minute_delta,
            }
        return ChoiceQuery(
            query_id=query_id,
            state={"step": state.step, "risk": state.risk, "minutes": state.minutes},
            instruction="Choose the action most likely to complete a reliable release.",
            criteria=criteria,
        )

    def apply(self, state: WorkflowState, action_key: str) -> WorkflowState:
        risk_delta, minute_delta = self.stages[state.step][action_key]
        return WorkflowState(
            step=state.step + 1,
            risk=state.risk + risk_delta,
            minutes=state.minutes + minute_delta,
            actions=(*state.actions, action_key),
        )

    def is_terminal(self, state: WorkflowState) -> bool:
        return state.step == len(self.stages)

    def terminal_outcome(self, state: WorkflowState) -> dict[str, Any]:
        success = state.risk <= 2
        return {
            "label": "success" if success else "failure",
            "terminal": True,
            "success": success,
            "risk": state.risk,
            "minutes": state.minutes,
        }

    def state_payload(self, state: WorkflowState) -> dict[str, Any]:
        return {
            "step": state.step,
            "risk": state.risk,
            "minutes": state.minutes,
            "actions": list(state.actions),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use TypeSafe/Jev instead of the offline smoke backend")
    args = parser.parse_args()

    backend = TypeSafeBackend() if args.live else HeuristicBackend()
    try:
        result = enumerate_probability_tree(
            ReleaseWorkflow(),
            backend,
            max_depth=3,
            question_batch_size=32,
            exploration_epsilon=0.01 if args.live else 0.0,
        )
        print("policy:", " -> ".join(derive_policy_path(result.leaves)))
        print("outcomes:", result.outcome_probability)
        print("mass error:", result.mass_error)
        print("provider requests:", result.usage.requests)
    finally:
        close = getattr(backend, "close", None)
        if close is not None:
            close()


if __name__ == "__main__":
    main()
