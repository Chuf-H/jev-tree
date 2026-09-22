from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            requests=self.requests + other.requests,
            latency_ms=self.latency_ms + other.latency_ms,
        )


@dataclass(frozen=True)
class ChoiceQuery:
    query_id: str
    state: dict[str, Any]
    instruction: str
    criteria: dict[str, Any]


@dataclass(frozen=True)
class ChoiceDistribution:
    query_id: str
    choice: str
    probabilities: dict[str, float]


@dataclass(frozen=True)
class BatchChoiceResult:
    choices: tuple[ChoiceDistribution, ...]
    model: str
    usage: Usage
    raw: dict[str, Any] | None = field(default=None, repr=False)


class DecisionBackend(Protocol):
    def choose_many(self, common_state: dict[str, Any], queries: Sequence[ChoiceQuery]) -> BatchChoiceResult:
        """Answer independent Choice heads, each carrying its own local state."""
