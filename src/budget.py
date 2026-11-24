"""Per-turn execution limits shared by the Pi transport and business tools."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from src.config import Settings


class BudgetExceeded(RuntimeError):
    def __init__(self):
        super().__init__("budget_exhausted")


@dataclass
class TurnBudget:
    settings: Settings
    clock: Callable[[], float] = time.monotonic
    model_attempts: int = 0
    tool_calls: int = 0
    reported_usage: dict = field(default_factory=dict)

    def __post_init__(self):
        self.started_at = self.clock()
        self.deadline = self.started_at + self.settings.turn_timeout_seconds

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if (
            remaining <= 0
            or self.reported_usage.get("total_tokens", 0) >= self.settings.max_reported_tokens
        ):
            raise BudgetExceeded()
        return remaining

    def model(self):
        self.remaining()
        if self.model_attempts >= self.settings.max_model_attempts:
            raise BudgetExceeded()
        self.model_attempts += 1

    def tool(self):
        self.remaining()
        if self.tool_calls >= self.settings.max_tool_calls:
            raise BudgetExceeded()
        self.tool_calls += 1
