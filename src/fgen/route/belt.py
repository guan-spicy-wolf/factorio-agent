"""Belt routing between item interfaces (plan-phase3a §3.4).

A link's path starts on the tile in front of the producer's output interface and ends
in one of two ways:

- **direct**: the path's last entity sits right behind the consumer's input tile and
  flows into it along the consumer's direction; both lanes keep their contents.
- **side-load** (the consumer interface names one lane): the consumer belt is extended
  upstream by a short **stub** (``STUB`` straight tiles, built once per consumer tile);
  the path ends on the side of the stub tile next to the consumer, flowing into it
  perpendicularly. Side-loading fills the lane on the side it comes from (an east-flowing
  belt fed from the north gets items on its left lane; checked in game, VERIFY §4), so
  the side is ``geom.belt.lane_side(flow, lane)``. The stub keeps one straight tile
  behind the side-load tile so the join stays a side-load, not a curve.

Search: :func:`fgen.route.astar.astar` over states ``(tile, incoming direction,
after_underground)``. Steps place a belt (straight or a 90 degree turn; a turn pays
``Costs.turn``); jump edges place an underground pair spanning ``2..max_distance``
tiles (``max_distance`` from the seed: the largest entrance-exit offset, VERIFY §4),
skipping whatever lies between. Entering an underground or turning right after one
is not allowed (an exit feeding the side of a belt would curve it unpredictably), nor
is ending a side-load on an underground exit. A route belt never sits where something
else pushes items into it, nor where an inserter or drill reaches.

The checks on rates and lanes are in :func:`consumer_report`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from fgen.data.schema import GameData
from fgen.gen.base import Infeasible
from fgen.geom import belt as beltgeom
from fgen.geom.belt import Lane
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import SIDE_LOAD_STUB, Interface, Placement, PortGroup, Predicate
from fgen.route.astar import NoPath, astar
from fgen.route.grid import RouteGrid
from fgen.site.model import Endpoint, Link

STUB = SIDE_LOAD_STUB  # straight tiles added upstream of a side-loaded consumer
_EPS = 1e-9


class RouteError(Infeasible):
    """A link could not be routed; ``link`` names it, ``where`` the closest tile."""

    def __init__(self, link: str, reason: str, where: Tile | None = None, **kw: Any) -> None:
        self.link = link
        self.where = where
        at = f" (got as far as ({where.x},{where.y}))" if where is not None else ""
        super().__init__(f"link {link}: {reason}{at}", **kw)


def plain_belt(gd: GameData, name: str) -> str:
    """The transport-belt of the same tier (speed) as ``name``."""
    e = gd.entity(name)
    if e.type == "transport-belt":
        return name
    for n, x in sorted(gd.entities.items()):
        if x.type == "transport-belt" and x.speed == e.speed:
            return n
    raise ValueError(f"no transport-belt with the speed of {name}")


def underground_for(gd: GameData, belt: str) -> str | None:
    speed = gd.entity(belt).speed
    for n, x in sorted(gd.entities.items()):
        if x.type == "underground-belt" and x.speed == speed:
            return n
    return None


def belt_placement(gd: GameData, name: str, t: Tile, d: Direction, **extra: Any) -> Placement:
    return Placement(name, t.center(), d, dict(extra))


# ---- consumers, stubs, reservations -------------------------------------------------


@dataclass
class Consumer:
    """All item links into one consumer belt tile."""

    tile: Tile
    direction: Direction
    belt: str
    ifaces: list[tuple[Endpoint, Interface]] = field(default_factory=list)
    links: list[tuple[Link, Literal["direct", "side"], Lane | None]] = field(default_factory=list)
    group: PortGroup | None = None  # the port group (lane requirements of the belt)
    label: str = ""  # "unit/group" (or the endpoint) for reports

    @property
    def side_loaded(self) -> bool:
        return any(m == "side" for _, m, _ in self.links)

    def stub_tiles(self) -> list[Tile]:
        return [self.tile.neighbor(self.direction.opposite, k) for k in range(1, STUB + 1)]

    def side_tile(self) -> Tile:
        return self.stub_tiles()[0]

    def approach(self, lane: Lane) -> Tile:
        return self.side_tile().neighbor(beltgeom.lane_side(self.direction, lane))

    def stub(self, gd: GameData) -> list[Placement]:
        return [belt_placement(gd, self.belt, t, self.direction) for t in self.stub_tiles()]


def link_mode(dst: Interface) -> tuple[Literal["direct", "side"], Lane | None]:
    """How a link joins the input ``dst``, from its contract: a side-load onto the lane it
    names when its ``space`` allows side approach, else straight in."""
    sp = dst.space
    if sp is None:
        raise Infeasible(f"item input at {dst.tile} has no space (approach) in its contract")
    if dst.lane in ("left", "right") and sp.approach in ("side", "both"):
        if sp.upstream < STUB:
            raise Infeasible(
                f"input at {dst.tile}: a side-load needs {STUB} tiles in front, the port "
                f"leaves {sp.upstream}"
            )
        return "side", dst.lane  # type: ignore[return-value]
    if sp.approach in ("direct", "both"):
        return "direct", None
    raise Infeasible(f"input at {dst.tile} takes only side-loads but names no single lane")


def reserve_ports(grid: RouteGrid, ports: Iterable[tuple[Endpoint, Interface]]) -> None:
    """Keep the ``space.upstream`` tiles in front of every item output / behind every
    item input, and the tile outside every fluid connection."""
    for ep, i in ports:
        if i.kind == "fluid":  # the tile outside the connection
            grid.reserve([i.tile.neighbor(i.direction)], str(ep))
        if i.kind != "item":
            continue
        if i.space is None:
            raise Infeasible(f"{ep}: item port has no space in its contract")
        d = i.direction if i.role == "output" else i.direction.opposite
        grid.reserve((i.tile.neighbor(d, k) for k in range(1, i.space.upstream + 1)), str(ep))


# ---- path search -------------------------------------------------------------------


def search(
    grid: RouteGrid,
    belt: str,
    start: Tile,
    din: Direction,
    goals: dict[tuple[Tile, Direction], bool],
    allowed: frozenset[str],
    label: str,
) -> list[Placement]:
    """Placements from ``start`` (items arrive moving ``din``) to a goal state
    ``(tile, incoming direction)``; the bool says the last step must be a belt."""
    gd = grid.gd
    ug = underground_for(gd, belt)
    maxd = int(gd.entity(ug).max_distance or 0) if ug else 0
    c = grid.costs
    goal_tiles = [t for t, _ in goals]

    def placeable(t: Tile, pred: Tile | None) -> bool:
        return (
            grid.free(t, allowed)
            and t not in grid.item_hazards
            and not grid.foreign_feeders(t, pred)
        )

    def is_goal(s: tuple[Tile, Direction, bool]) -> bool:
        t, d, after_ug = s
        need_belt = goals.get((t, d))
        return need_belt is not None and not (need_belt and after_ug)

    def h(s: tuple[Tile, Direction, bool]) -> float:
        t = s[0]
        return c.step * min(abs(t.x - g.x) + abs(t.y - g.y) for g in goal_tiles)

    def expand(s: tuple[Tile, Direction, bool]):
        t, d, after_ug = s
        pred = t.neighbor(d.opposite)
        if not placeable(t, pred):
            return
        outs = [d] if after_ug else [d, d.rotate_cw(), d.rotate_ccw()]
        for o in outs:
            cost = grid.tile_cost(t) + (c.turn if o != d else 0.0)
            yield (t.neighbor(o), o, False), cost, [belt_placement(gd, belt, t, o)]
        if ug is None or grid.in_span(ug, d, t):
            return
        for n in range(1, maxd + 1):
            e = t.neighbor(d, n)
            if grid.same_line_end(ug, d, e):
                break  # another end of the same kind on this line would pair instead
            if n < 2 or not placeable(e, None) or grid.in_span(ug, d, e):
                continue
            cost = grid.tile_cost(t) + grid.tile_cost(e) + c.step * (n - 1) + c.underground
            yield (
                (e.neighbor(d), d, True),
                cost,
                [
                    belt_placement(gd, ug, t, d, type="input"),
                    belt_placement(gd, ug, e, d, type="output"),
                ],
            )

    try:
        path = astar([(start, din, False)], is_goal, expand, h)
    except NoPath as exc:
        where = None
        if exc.closest:
            t, d, _ = exc.closest
            where = t if placeable(t, t.neighbor(d.opposite)) else t.neighbor(d.opposite)
        raise RouteError(label, f"no belt path ({exc.reason or 'unreachable'})", where) from None
    out = [p for step in path.actions for p in step]
    seen: set[Tile] = set()
    for p in out:
        t = p.position.floor()
        if t in seen:
            raise RouteError(label, f"belt path crosses itself at {t}", t)
        seen.add(t)
    return out


# ---- rate and lane checks -------------------------------------------------------


@dataclass
class Supply:
    link: Link
    producer: Interface
    mode: Literal["direct", "side"]
    lane: Lane | None  # lane filled by a side-load


def _lanes_of(i: Interface) -> tuple[Lane, ...]:
    return ("left", "right") if i.lane in (None, "both") else (i.lane,)  # type: ignore[return-value]


def consumer_report(
    gd: GameData,
    consumer: Consumer,
    supplies: list[Supply],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Check the consumer's ``requires`` (its port group's and each port's) against what
    the links bring, from the producers' contracts only: per-lane fixed rates
    (``RateProfile.lanes``), on-demand supply with its limit, the producer's belt
    (``Interface.entity``).

    Returns ``(checks, problems, warnings)``: ``checks`` one dict per predicate
    (``ok`` None for ``Compressed``, which is only recorded); ``problems`` are capacity
    violations (the caller raises); ``warnings`` e.g. a slower upstream belt or an
    unmet ``MinRate`` (the link works, the consumer will not reach its target).
    """
    from fgen.layout.model import Compressed, LaneMaterial, MinRate

    lane_cap = beltgeom.lane_items_per_second(gd, consumer.belt)
    belt_cap = beltgeom.items_per_second(gd, consumer.belt)
    materials: dict[Lane, set[str]] = {"left": set(), "right": set()}
    fixed: dict[Lane, float] = {"left": 0.0, "right": 0.0}
    both_fixed = 0.0  # fixed 'both' producers without a per-lane split
    elastic: dict[Lane, float] = {"left": 0.0, "right": 0.0}  # on-demand limits
    for s in supplies:
        prod = s.producer
        lanes = (s.lane,) if s.mode == "side" else _lanes_of(prod)
        for ln in lanes:
            if prod.material is not None:
                materials[ln].add(prod.material)  # type: ignore[index]
        rp = prod.rate_profile
        if prod.on_demand():
            assert rp is not None
            for ln in lanes:
                lim = rp.limit if s.mode == "side" else prod.lane_rate(ln)  # type: ignore[arg-type]
                elastic[ln] += math.inf if lim is None else lim  # type: ignore[index]
            continue
        r = prod.fixed_rate()
        if r is None:
            continue
        if s.mode == "side":
            fixed[s.lane] += r  # type: ignore[index]
            continue
        split = {ln: prod.lane_rate(ln) for ln in ("left", "right")}
        if all(v is not None for v in split.values()):
            for ln, v in split.items():
                fixed[ln] += v or 0.0  # type: ignore[index]
        else:
            both_fixed += r
    problems: list[str] = []
    warnings: list[str] = []
    for ln in ("left", "right"):
        if fixed[ln] > lane_cap + _EPS:  # type: ignore[index]
            problems.append(
                f"{fixed[ln]:g}/s onto the {ln} lane of {consumer.belt} at "  # type: ignore[index]
                f"({consumer.tile.x},{consumer.tile.y}), which carries {lane_cap:g}/s"
            )
    total = fixed["left"] + fixed["right"] + both_fixed
    if total > belt_cap + _EPS:
        problems.append(f"{total:g}/s into one {consumer.belt} ({belt_cap:g}/s)")
    for s in supplies:
        pb = s.producer.entity
        if pb is not None and (gd.entity(pb).speed or 0) < (gd.entity(consumer.belt).speed or 0):
            warnings.append(
                f"{s.link.label}: producer belt {pb} is slower than the consumer's {consumer.belt}"
            )

    def supplied(lanes: tuple[Lane, ...]) -> float:
        out = 0.0
        for ln in lanes:
            out += min(lane_cap, fixed[ln] + elastic[ln]) if elastic[ln] else fixed[ln]
        if len(lanes) == 2:
            out += both_fixed
            out = min(out, belt_cap)
        else:
            # a 'both' producer without a lane split may put up to half on each lane
            out += both_fixed / 2
        return out

    reqs: list[tuple[str, Predicate, Interface | None]] = []
    if consumer.group is not None:
        reqs += [(consumer.label, r, None) for r in consumer.group.requires]
    for ep, i in consumer.ifaces:
        reqs += [(str(ep), r, i) for r in i.requires]
    checks: list[dict[str, Any]] = []
    for who, r, i in reqs:
        if isinstance(r, LaneMaterial):
            got = sorted(materials[r.lane])
            ok = set(got) <= {r.material}
            checks.append(
                {
                    "interface": who,
                    "predicate": f"LaneMaterial({r.lane}, {r.material})",
                    "ok": ok,
                    "detail": f"{r.lane} lane gets {got or 'nothing'}",
                }
            )
            if not ok:
                problems.append(
                    f"{who}: {r.lane} lane must carry only {r.material}, links bring {got}"
                )
            elif not got:
                warnings.append(f"{who}: nothing arrives on the {r.lane} lane")
        elif isinstance(r, MinRate):
            got_rate = supplied(_lanes_of(i) if i is not None else ("left", "right"))
            ok = got_rate + _EPS >= r.value
            checks.append(
                {
                    "interface": who,
                    "predicate": f"MinRate({r.value:g})",
                    "ok": ok,
                    "detail": f"links supply {got_rate:g}/s",
                }
            )
            if not ok:
                warnings.append(f"{who}: needs {r.value:g}/s, links supply {got_rate:g}/s")
        elif isinstance(r, Compressed):
            checks.append({"interface": who, "predicate": "Compressed", "ok": None, "detail": ""})
    return checks, problems, warnings


def capacity_suggestions(gd: GameData, consumer: Consumer, need_lane: float) -> list[str]:
    out = []
    for n, e in sorted(gd.entities.items(), key=lambda kv: kv[1].speed or 0):
        if e.type != "transport-belt" or (e.speed or 0) <= (gd.entity(consumer.belt).speed or 0):
            continue
        if beltgeom.lane_items_per_second(gd, n) + _EPS >= need_lane:
            out.append(f"consumer belt={n!r}")
    out.append("split the producers over two consumer inputs")
    return out
