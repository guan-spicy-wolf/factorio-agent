"""Pole chains joining every power interface of a site (plan-phase3a §3.4).

Each power interface is a pole of its unit (``Interface.tile`` = the pole's tile). The
interfaces are joined by a Euclidean minimum spanning tree (Kruskal); each tree edge
already in wire reach needs nothing, otherwise a chain of poles is searched with A*
over free tiles, one hop = one pole, a hop never longer than the wire reach of the two
poles it joins (``geom.pole.wire_reaches``: the smaller ``maximum_wire_distance``).
Wires cross anything (water, cliffs, belts), so the chain only needs free tiles to
stand on; it detours around blocked areas by itself. The chain pole is ``pole_name``
or, per edge, the endpoint pole with the shorter reach.
"""

from __future__ import annotations

import math
from typing import Any

from fgen.data.schema import GameData
from fgen.geom import footprint, pole
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout.model import Placement
from fgen.route.astar import NoPath, astar
from fgen.route.belt import RouteError
from fgen.route.grid import RouteGrid

_EPS = 1e-9


def power_poles(site: Any, gd: GameData) -> list[tuple[str, str, Vec2]]:
    """(endpoint label, pole name, centre) of every power interface in the site."""
    out = []
    for ep, i in site.interfaces():
        if i.kind != "power":
            continue
        lay = site.world(ep.unit).layout
        p = next(
            (
                q
                for q in lay.placements
                if gd.entity(q.name).type == "electric-pole" and i.tile in q.tiles(gd)
            ),
            None,
        )
        if p is None:
            raise RouteError(str(ep), f"no pole under the power interface {i.tile}")
        out.append((str(ep), p.name, p.position))
    return out


def mst(points: list[Vec2]) -> list[tuple[int, int]]:
    edges = sorted(
        (points[a].distance(points[b]), a, b)
        for a in range(len(points))
        for b in range(a + 1, len(points))
    )
    parent = list(range(len(points)))

    def find(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    out = []
    for _, a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            out.append((a, b))
    return out


def chain(
    grid: RouteGrid,
    name: str,
    a: tuple[str, Vec2],
    b: tuple[str, Vec2],
    label: str,
) -> list[Placement]:
    """Poles of type ``name`` from pole ``a`` to pole ``b`` (both already built)."""
    gd = grid.gd
    if pole.wire_reaches(gd, a[0], a[1], b[0], b[1]):
        return []
    reach = pole.wire_distance(gd, name, name)
    end_reach = pole.wire_distance(gd, name, b[0])
    pw, ph = footprint.size(gd, name)
    ra = math.ceil(reach) + 1
    lo_x = min(a[1].x, b[1].x) - 3 * reach
    hi_x = max(a[1].x, b[1].x) + 3 * reach
    lo_y = min(a[1].y, b[1].y) - 3 * reach
    hi_y = max(a[1].y, b[1].y) + 3 * reach

    def fits(anchor: Tile) -> Vec2 | None:
        c = footprint.center_for_tile_anchor(gd, name, anchor)
        if not (lo_x <= c.x <= hi_x and lo_y <= c.y <= hi_y):
            return None
        ts = footprint.tiles_of(gd, name, c, Direction.N)
        if all(grid.free(t) and t not in grid.item_hazards for t in ts):
            return c
        return None

    start = ("start", a[1])

    def is_goal(s: tuple[str, Vec2]) -> bool:
        return s[0] != "start" and pole.wire_reaches(gd, name, s[1], b[0], b[1])

    def h(s: tuple[str, Vec2]) -> float:
        return max(0.0, (s[1].distance(b[1]) - end_reach) / reach)

    def expand(s: tuple[str, Vec2]):
        kind, c = s
        src = a[0] if kind == "start" else name
        base = c.floor()
        for dx in range(-ra, ra + 1):
            for dy in range(-ra, ra + 1):
                anchor = Tile(base.x + dx - (pw - 1) // 2, base.y + dy - (ph - 1) // 2)
                n = fits(anchor)
                if n is None or not pole.wire_reaches(gd, src, c, name, n):
                    continue
                # one pole per hop; prefer long hops (fewer poles, straighter chains)
                yield ("pole", n), 1.0 + 1e-3 * (reach - c.distance(n)), n

    try:
        path = astar([start], is_goal, expand, h, max_expansions=20_000)
    except NoPath as exc:
        where = exc.closest[1].floor() if exc.closest else None
        raise RouteError(label, f"no pole chain ({exc.reason or 'unreachable'})", where) from None
    return [Placement(name, c) for c in path.actions]


def route_site(site: Any, gd: GameData, grid: RouteGrid, conn: Any, pole_name: str | None) -> None:
    from fgen.route.connect import LinkRoute

    poles = power_poles(site, gd)
    if len(poles) < 2:
        return
    for a, b in mst([p[2] for p in poles]):
        (la, na, ca), (lb, nb, cb) = poles[a], poles[b]
        name = pole_name or (
            na if pole.wire_distance(gd, na, na) <= pole.wire_distance(gd, nb, nb) + _EPS else nb
        )
        label = f"power {la}-{lb}"
        ps = chain(grid, name, (na, ca), (nb, cb), label)
        grid.add(ps, label)
        conn.routes.append(LinkRoute(label, "power", ps))
        conn.order.append(label)
