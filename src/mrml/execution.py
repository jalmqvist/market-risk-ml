"""Demo-only execution handoff contract."""

from __future__ import annotations

from typing import Protocol


class DemoExecutionHandoff(Protocol):
    """An injected practice-account executor; no live implementation is provided."""

    def submit(
        self, *, client_order_id: str, pair: str, direction: str, units: float
    ) -> dict[str, str]: ...
