from __future__ import annotations

import math
import os
import time
from typing import Any, Sequence

from .types import BatchChoiceResult, ChoiceDistribution, ChoiceQuery, DecisionBackend, Usage


def _normalized_probabilities(labels: Sequence[str], values: dict[str, Any]) -> dict[str, float]:
    probabilities = {label: max(0.0, float(values.get(label, 0.0))) for label in labels}
    total = sum(probabilities.values())
    if not math.isfinite(total) or total <= 0:
        uniform = 1.0 / len(labels)
        return {label: uniform for label in labels}
    return {label: value / total for label, value in probabilities.items()}


class TypeSafeBackend(DecisionBackend):
    """Thin adapter around the official TypeSafe Python SDK."""

    def __init__(self, *, api_key: str | None = None, model: str = "jev-latest", timeout: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("TYPESAFE_API_KEY is required")
        self.model = model
        self.timeout = timeout
        from typesafe_sdk import TypeSafeClient

        self._client = TypeSafeClient(api_key=self.api_key, model=self.model, timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def choose_many(self, common_state: dict[str, Any], queries: Sequence[ChoiceQuery]) -> BatchChoiceResult:
        if not queries:
            return BatchChoiceResult(choices=(), model=self.model, usage=Usage())
        local_results: list[ChoiceDistribution] = []
        remote_queries: list[ChoiceQuery] = []
        for query in queries:
            if len(query.criteria) == 1:
                label = next(iter(query.criteria))
                local_results.append(ChoiceDistribution(query.query_id, label, {label: 1.0}))
            else:
                remote_queries.append(query)
        if not remote_queries:
            return BatchChoiceResult(tuple(local_results), self.model, Usage())

        from typesafe_sdk import Choice

        questions = {
            query.query_id: Choice(
                instructions={"task": query.instruction, "local_state": query.state},
                criteria=query.criteria,
            )
            for query in remote_queries
        }
        started = time.perf_counter()
        response = self._client.system_one(state=common_state, questions=questions)
        latency_ms = (time.perf_counter() - started) * 1000.0
        remote_results: list[ChoiceDistribution] = []
        for query in remote_queries:
            answer = response.choices[query.query_id]
            probabilities = _normalized_probabilities(tuple(query.criteria), dict(answer.probabilities))
            choice = answer.choice if answer.choice in probabilities else max(probabilities, key=probabilities.get)
            remote_results.append(ChoiceDistribution(query.query_id, choice, probabilities))
        usage = Usage(
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            requests=1,
            latency_ms=latency_ms,
        )
        order = {query.query_id: index for index, query in enumerate(queries)}
        combined = sorted([*local_results, *remote_results], key=lambda item: order[item.query_id])
        raw = response.model_dump(mode="json")
        return BatchChoiceResult(tuple(combined), response.model, usage, raw)


class HeuristicBackend(DecisionBackend):
    """Deterministic offline backend used only for tests and plumbing smoke runs."""

    def choose_many(self, common_state: dict[str, Any], queries: Sequence[ChoiceQuery]) -> BatchChoiceResult:
        results: list[ChoiceDistribution] = []
        for query in queries:
            labels = list(query.criteria)
            raw_scores = {
                label: math.exp(-float(query.criteria[label].get("goal_distance", 0)))
                if isinstance(query.criteria[label], dict)
                else 1.0
                for label in labels
            }
            probabilities = _normalized_probabilities(labels, raw_scores)
            choice = max(labels, key=probabilities.get)
            results.append(ChoiceDistribution(query.query_id, choice, probabilities))
        return BatchChoiceResult(tuple(results), "heuristic-offline", Usage(), raw=None)
