"""Tracks how recently each asset has been tapped for flexibility, so the
clearing engine can avoid hitting the same asset repeatedly just because
it's cheapest every time.
"""
from collections import defaultdict, deque


class RecencyTracker:
    def __init__(self, window=20):
        self.window = window
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._tick = 0

    def record(self, asset_id: str) -> None:
        self._tick += 1
        self._history[asset_id].append(self._tick)

    def count(self, asset_id: str, lookback: int = 5) -> int:
        """How many times this asset has been used in the last `lookback` ticks."""
        if self._tick == 0:
            return 0
        return sum(1 for t in self._history[asset_id] if self._tick - t <= lookback)
