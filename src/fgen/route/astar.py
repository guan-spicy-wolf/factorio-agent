"""Grid A* with turn costs and jump edges (plan-phase3a §3.4).

The search is generic: a state is any hashable value (the routers use
``(tile, incoming direction, flag)``), ``expand`` yields ``(next state, step cost,
action)`` where an action is whatever the router needs to rebuild the path (the
placements of that step). Jump edges (underground belts and pipes) are just actions
that move several tiles at once. Ties are broken by insertion order, so the result is
deterministic.

If no goal is reachable, :class:`NoPath` carries the expanded state closest to the
goal by the heuristic: the router reports it as "got as far as ...".
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import Any


class NoPath(Exception):
    def __init__(self, closest: Any, expanded: int, reason: str = "") -> None:
        self.closest = closest
        self.expanded = expanded
        self.reason = reason
        super().__init__(reason or f"no path after {expanded} expansions")


@dataclass
class Path:
    actions: list[Any]
    cost: float
    goal: Any
    expanded: int


def astar[S: Hashable](
    starts: Iterable[S],
    is_goal: Callable[[S], bool],
    expand: Callable[[S], Iterable[tuple[S, float, Any]]],
    heuristic: Callable[[S], float],
    *,
    max_expansions: int = 400_000,
) -> Path:
    counter = itertools.count()
    best: dict[S, float] = {}
    parent: dict[S, tuple[S | None, Any]] = {}
    heap: list[tuple[float, int, float, S]] = []
    for s in starts:
        best[s] = 0.0
        parent[s] = (None, None)
        heapq.heappush(heap, (heuristic(s), next(counter), 0.0, s))
    closest: tuple[float, S] | None = None
    expanded = 0
    while heap:
        _, _, g, s = heapq.heappop(heap)
        if g > best.get(s, float("inf")):
            continue
        if is_goal(s):
            actions = []
            cur: S | None = s
            while cur is not None:
                prev, act = parent[cur]
                if prev is not None:
                    actions.append(act)
                cur = prev
            actions.reverse()
            return Path(actions, g, s, expanded)
        expanded += 1
        h = heuristic(s)
        if closest is None or h < closest[0]:
            closest = (h, s)
        if expanded > max_expansions:
            raise NoPath(closest[1], expanded, f"gave up after {expanded} expansions")
        for n, c, act in expand(s):
            ng = g + c
            if ng < best.get(n, float("inf")):
                best[n] = ng
                parent[n] = (s, act)
                heapq.heappush(heap, (ng + heuristic(n), next(counter), ng, n))
    raise NoPath(closest[1] if closest else None, expanded)
