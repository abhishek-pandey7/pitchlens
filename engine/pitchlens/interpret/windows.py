"""Trailing time windows and exponentially decayed sums, the building blocks of rolling team metrics."""

from __future__ import annotations

from collections import defaultdict, deque


class EventWindow:
    """Timestamped amounts per key, summed over the trailing `window_ms`. Items must arrive in time order."""

    def __init__(self, window_ms: int) -> None:
        self.window_ms = window_ms
        self._items: deque[tuple[int, str, float]] = deque()
        self._totals: defaultdict[str, float] = defaultdict(float)

    def add(self, ms: int, key: str, amount: float = 1.0) -> None:
        self._items.append((ms, key, amount))
        self._totals[key] += amount

    def expire(self, now_ms: int) -> None:
        while self._items and now_ms - self._items[0][0] > self.window_ms:
            _, key, amount = self._items.popleft()
            self._totals[key] -= amount

    def total(self, key: str, now_ms: int) -> float:
        self.expire(now_ms)
        return max(0.0, self._totals[key])

    def span_ms(self, now_ms: int, since_ms: int = 0) -> int:
        """Length of the window actually covered so far (shorter than window_ms early in a match)."""
        return max(1, min(self.window_ms, now_ms - since_ms))


class DecayedSum:
    """Sum of contributions that halve every `half_life_ms`."""

    def __init__(self, half_life_ms: int) -> None:
        self.half_life_ms = half_life_ms
        self._value = 0.0
        self._at = 0

    def value(self, now_ms: int) -> float:
        if now_ms <= self._at:
            return self._value
        return self._value * 0.5 ** ((now_ms - self._at) / self.half_life_ms)

    def add(self, now_ms: int, amount: float) -> None:
        self._value = self.value(now_ms) + amount
        self._at = max(now_ms, self._at)


class SegmentShare:
    """Share of trailing time owned by each key, from a sequence of (start_ms, key) ownership changes."""

    def __init__(self, window_ms: int) -> None:
        self.window_ms = window_ms
        self._segments: deque[tuple[int, str]] = deque()

    def change(self, ms: int, key: str) -> None:
        if not self._segments or self._segments[-1][1] != key:
            self._segments.append((ms, key))

    def shares(self, now_ms: int) -> dict[str, float]:
        lo = now_ms - self.window_ms
        while len(self._segments) > 1 and self._segments[1][0] <= lo:
            self._segments.popleft()
        owned: defaultdict[str, float] = defaultdict(float)
        segs = list(self._segments)
        for i, (start, key) in enumerate(segs):
            end = segs[i + 1][0] if i + 1 < len(segs) else now_ms
            owned[key] += max(0, end - max(start, lo))
        total = sum(owned.values())
        return {k: v / total for k, v in owned.items()} if total else {}
