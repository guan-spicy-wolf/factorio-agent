"""Electric pole supply areas and wire reach."""

from __future__ import annotations

import math

from fgen.data.schema import GameData
from fgen.geom.vec import Tile, Vec2

_EPS = 1e-6


def _require(gd: GameData, name: str) -> tuple[float, float]:
    e = gd.entity(name)
    if e.supply_area_distance is None or e.maximum_wire_distance is None:
        raise ValueError(f"{name} is not an electric pole")
    return e.supply_area_distance, e.maximum_wire_distance


def supply_range(gd: GameData, name: str, pos: Vec2) -> tuple[int, int, int, int]:
    """Inclusive tile range ``(x0, y0, x1, y1)`` fully inside the supply square."""
    sad, _ = _require(gd, name)
    x0 = math.ceil(pos.x - sad - _EPS)
    y0 = math.ceil(pos.y - sad - _EPS)
    x1 = math.floor(pos.x + sad + _EPS) - 1
    y1 = math.floor(pos.y + sad + _EPS) - 1
    return x0, y0, x1, y1


def supply_tiles(gd: GameData, name: str, pos: Vec2) -> frozenset[Tile]:
    x0, y0, x1, y1 = supply_range(gd, name, pos)
    return frozenset(Tile(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def wire_distance(gd: GameData, a_name: str, b_name: str) -> float:
    return min(_require(gd, a_name)[1], _require(gd, b_name)[1])


def wire_reaches(gd: GameData, a_name: str, a_pos: Vec2, b_name: str, b_pos: Vec2) -> bool:
    """Centre distance <= the smaller ``maximum_wire_distance`` of the two poles."""
    return a_pos.distance(b_pos) <= wire_distance(gd, a_name, b_name) + _EPS


def copper_tree(gd: GameData, poles: list[tuple[str, Vec2]]) -> list[tuple[int, int]]:
    """Copper wires to draw between poles: a minimum spanning forest of the wire-reach
    graph (Kruskal, shortest first, ties by index).

    Blueprints must spell the wires out: on 2.0.76 poles built from a blueprint without
    ``wires`` stay unconnected even when in reach (see VERIFY.md). Returns index pairs
    ``(i, j)`` with ``i < j``.
    """
    edges = sorted(
        (a_pos.distance(b_pos), i, j)
        for i, (a_name, a_pos) in enumerate(poles)
        for j, (b_name, b_pos) in enumerate(poles)
        if i < j and wire_reaches(gd, a_name, a_pos, b_name, b_pos)
    )
    parent = list(range(len(poles)))

    def find(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    out = []
    for _, i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
            out.append((i, j))
    return out
