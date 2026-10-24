"""Pipe routing between fluid interfaces (plan-phase3a §3.4).

A fluid interface is a fluid connection of its unit: ``tile`` is the entity tile that
carries the connection, ``direction`` points outward. A link's pipe runs from the tile
outside the producer's connection to the tile outside the consumer's; both ends are
plain pipes (they connect on all four sides).

Search states are ``(tile, direction of travel, after_underground)``. A plain pipe may
stand on a free tile only if no *foreign* fluid connection faces it (a pipe connects
on all four sides, so it would join that system; the producer's and consumer's own
connections are not foreign). Jump edges place a pipe-to-ground pair: the entrance's
normal connection faces back along the path, the exit's faces forward, the underground
offset is ``2..max_underground_distance`` (the largest offset the game pairs: 10 for
the pipe-to-ground, checked on 2.1.23, VERIFY §10). A pipe-to-ground connects only on
its normal side, so its ends may stand next to foreign pipes. No other pipe-to-ground
of the same name and axis may lie between the ends.
"""

from __future__ import annotations

from typing import Any

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Placement
from fgen.route.astar import NoPath, astar
from fgen.route.belt import RouteError
from fgen.route.grid import RouteGrid

PIPE = "pipe"
PIPE_TO_GROUND = "pipe-to-ground"


def search(
    grid: RouteGrid,
    start: Tile,
    din: Direction,
    goal: Tile,
    own: frozenset[tuple[Tile, Direction]],
    allowed: frozenset[str],
    label: str,
    pipe: str = PIPE,
    underground: str = PIPE_TO_GROUND,
) -> list[Placement]:
    gd = grid.gd
    maxd = int(gd.entity(underground).max_underground_distance or 0)
    c = grid.costs

    def free(t: Tile) -> bool:
        return grid.free(t, allowed)

    def pipe_ok(t: Tile) -> bool:
        return free(t) and not grid.foreign_port_facing(t, own)

    if not pipe_ok(goal):
        raise RouteError(label, f"the tile outside the consumer's connection {goal} is not usable")

    def is_goal(s: tuple[Tile, Direction, bool]) -> bool:
        return s[0] == goal

    def h(s: tuple[Tile, Direction, bool]) -> float:
        t = s[0]
        return c.step * (abs(t.x - goal.x) + abs(t.y - goal.y))

    def expand(s: tuple[Tile, Direction, bool]):
        t, d, _ = s
        if pipe_ok(t):
            for o in (d, d.rotate_cw(), d.rotate_ccw()):
                cost = grid.tile_cost(t) + (c.turn if o != d else 0.0)
                yield (t.neighbor(o), o, False), cost, [Placement(pipe, t.center())]
        if not free(t) or grid.in_span(underground, d, t):
            return
        for n in range(1, maxd + 1):
            e = t.neighbor(d, n)
            if grid.same_line_end(underground, d, e):
                break
            if n < 2 or not free(e) or grid.in_span(underground, d, e):
                continue
            cost = grid.tile_cost(t) + grid.tile_cost(e) + c.step * (n - 1) + c.underground
            yield (
                (e.neighbor(d), d, True),
                cost,
                [
                    Placement(underground, t.center(), d.opposite),
                    Placement(underground, e.center(), d),
                ],
            )

    try:
        path = astar([(start, din, False)], is_goal, expand, h)
    except NoPath as exc:
        where = exc.closest[0] if exc.closest else None
        raise RouteError(label, f"no pipe path ({exc.reason or 'unreachable'})", where) from None
    out = [p for step in path.actions for p in step] + [Placement(pipe, goal.center())]
    seen: set[Tile] = set()
    for p in out:
        t = p.position.floor()
        if t in seen:
            raise RouteError(label, f"pipe path crosses itself at {t}", t)
        seen.add(t)
    return out


def route_links(site: Any, gd: GameData, grid: RouteGrid, conn: Any) -> None:
    from fgen.route.connect import LinkRoute

    for ln in site.links:
        src = site.interface(ln.src)
        if src.kind != "fluid":
            continue
        dst = site.interface(ln.dst)
        if src.material is not None and dst.material is not None and src.material != dst.material:
            raise RouteError(ln.label, f"{src.material} into a {dst.material} input")
        # the link's own two connections (from the contracts) are not foreign
        own = frozenset({(src.tile, src.direction), (dst.tile, dst.direction)})
        allowed = frozenset({str(ln.src), str(ln.dst)})
        ps = search(
            grid,
            src.tile.neighbor(src.direction),
            src.direction,
            dst.tile.neighbor(dst.direction),
            own,
            allowed,
            ln.label,
        )
        f = src.material or dst.material
        grid.add(ps, ln.label, fluid_all=f)
        conn.routes.append(LinkRoute(ln.label, "fluid", ps, fluid=f))
        conn.order.append(ln.label)
