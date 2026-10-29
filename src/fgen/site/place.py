"""Minimal placement rule (plan-phase3a §3.5): not a siting agent.

``find_spot`` scans square rings around an anchor tile (nearest ring first, a fixed
order inside each ring) for the first origin where the unit, posed by one of the
candidate poses, fits on free buildable tiles with a margin around it and with the
tiles in front of its ports clear (as many as each port's contract ``space`` asks).
``place_port`` instead computes the origin and pose that put one chosen interface on a
given tile facing a given direction (used for fixtures that sit right at the port they
serve).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Interface, PlacedUnit, Pose, Unit
from fgen.layout.transform import place_unit
from fgen.terrain.model import Terrain


def port_tiles(i: Interface, clearance: int | None = None) -> list[Tile]:
    """Tiles in front of a port that other units must leave free: for an item port the
    ``space.upstream`` tiles of its contract (``clearance`` overrides), for a fluid
    connection the tile outside it."""
    if i.kind == "item":
        if clearance is None:
            if i.space is None:
                raise ValueError(f"item port at {i.tile} has no space in its contract")
            clearance = i.space.upstream
        d = i.direction if i.role == "output" else i.direction.opposite
        return [i.tile.neighbor(d, k) for k in range(1, clearance + 1)]
    if i.kind == "fluid":
        return [i.tile.neighbor(i.direction)]
    return []


def footprint_ok(
    unit: Unit,
    gd: GameData,
    free: Callable[[Tile], bool],
    margin: int = 1,
    clearance: int | None = None,
) -> bool:
    tiles = unit.layout.tiles(gd)
    need: set[Tile] = set(tiles)
    for t in tiles:
        for dx in range(-margin, margin + 1):
            for dy in range(-margin, margin + 1):
                need.add(Tile(t.x + dx, t.y + dy))
    for i in unit.interfaces:
        need.update(port_tiles(i, clearance))
    return all(free(t) for t in need)


def rings(anchor: Tile, max_radius: int) -> Iterable[Tile]:
    yield anchor
    for r in range(1, max_radius + 1):
        for dx in range(-r, r + 1):
            yield Tile(anchor.x + dx, anchor.y - r)
        for dy in range(-r + 1, r + 1):
            yield Tile(anchor.x + r, anchor.y + dy)
        for dx in range(r - 1, -r - 1, -1):
            yield Tile(anchor.x + dx, anchor.y + r)
        for dy in range(r - 1, -r, -1):
            yield Tile(anchor.x - r, anchor.y + dy)


def find_spot(
    name: str,
    unit: Unit,
    gd: GameData,
    free: Callable[[Tile], bool],
    anchor: Tile,
    *,
    poses: Iterable[Pose] = (Pose(),),
    margin: int = 1,
    clearance: int | None = None,
    max_radius: int = 80,
) -> PlacedUnit:
    """First fitting placement of ``unit`` whose origin is nearest ``anchor``.
    ``free(tile)`` says whether a tile may be used (buildable and unoccupied)."""
    poses = list(poses)
    for origin in rings(anchor, max_radius):
        for pose in poses:
            w = place_unit(unit, origin, pose)
            if footprint_ok(w, gd, free, margin, clearance):
                return PlacedUnit(name, unit, origin, pose)
    raise ValueError(f"no room for {name} within {max_radius} tiles of {anchor}")


def pose_for(local: Direction, world: Direction) -> Pose:
    """The rotation that turns a ``local`` direction into ``world``."""
    q = (world.quarter_turns - local.quarter_turns) % 4
    return Pose(Direction.N.rotate_cw(q))


def place_port(name: str, unit: Unit, iface: int, tile: Tile, direction: Direction) -> PlacedUnit:
    """Place ``unit`` so its interface ``iface`` lands on ``tile`` facing ``direction``."""
    i = unit.interfaces[iface]
    pose = pose_for(i.direction, direction)
    w = i.world(Tile(0, 0), pose)
    return PlacedUnit(name, unit, Tile(tile.x - w.tile.x, tile.y - w.tile.y), pose)


# ---- link-driven placement (plan-phase7 task A) ------------------------------------------
#
# A free unit goes where the links it has to already placed units are cheapest: every
# candidate (origin, pose) in a window around those units' ports gets an estimated belt
# length for each link (Manhattan distance between the link's two ends, a penalty per
# turn the two directions force, a detour when the goal lies behind the start) plus a
# penalty per footprint tile on a resource; the cheapest candidate that fits (free
# buildable tiles with a margin, port tiles clear) wins. The estimate reads only the two
# ports' contracts (tile, direction, lane, ``space``) the way the router does
# (``route.belt.link_mode``): where a belt leaves a producer and where it has to arrive
# at a consumer (straight in, or beside the side-load stub).


@dataclass(frozen=True)
class PlaceCosts:
    """Weights of the placement estimate (layout preferences, not game data)."""

    step: float = 1.0  # per tile of estimated belt
    turn: float = 2.0  # per 90 degree turn the two port directions force
    detour: float = 2.0  # per tile of overshoot when the goal lies behind the start
    resource: float = 2.0  # per footprint tile on a resource (keeps units off ore)
    slack: int = 12  # window radius beyond the unit's longer side


def link_ends(src: Interface, dst: Interface) -> tuple[Tile, Direction, Tile, Direction]:
    """``(start, start direction, goal, goal direction)`` of the belt a link from output
    ``src`` to input ``dst`` would run: it leaves the producer on the tile in front of its
    port and arrives, for a direct join, on the tile behind the consumer's port moving the
    port's way, for a side-load, on the approach tile beside the stub moving toward it."""
    from fgen.geom import belt as beltgeom
    from fgen.route.belt import link_mode

    start = src.tile.neighbor(src.direction)
    if src.kind != "item":
        return src.tile, src.direction, dst.tile, dst.direction
    mode, lane = link_mode(dst)
    before = dst.tile.neighbor(dst.direction.opposite)
    if mode == "side":
        assert lane is not None
        side = beltgeom.lane_side(dst.direction, lane)
        return start, src.direction, before.neighbor(side), side.opposite
    return start, src.direction, before, dst.direction


def estimate(start: Tile, din: Direction, goal: Tile, dgoal: Direction, costs: PlaceCosts) -> float:
    """Lower-bound-ish cost of a belt from ``start`` (items moving ``din``) to ``goal``
    (arriving moving ``dgoal``): Manhattan length, forced turns, overshoot detours."""
    dx, dy = goal.x - start.x, goal.y - start.y
    f = dx * din.tile_offset().x + dy * din.tile_offset().y  # forward component
    cw = din.rotate_cw().tile_offset()
    lat = dx * cw.x + dy * cw.y  # lateral component, clockwise positive
    length = abs(dx) + abs(dy)
    turns, over = 0, 0
    if dgoal == din:
        if f < 0:
            turns, over = 4, -f + 1  # go forward, come back, go forward again
            if lat == 0:
                over += 1
        elif lat != 0:
            turns = 2
    elif dgoal == din.opposite:
        turns = 2
        if lat == 0:
            over = 1
        if f < 0:
            over += -f
    else:
        want = 1 if dgoal == din.rotate_cw() else -1  # the goal must lie on that side
        if f >= 0 and lat * want > 0:
            turns = 1
        else:
            turns = 3
            over = max(0, -f) + (1 if lat * want <= 0 else 0)
    return costs.step * length + costs.turn * turns + costs.detour * over


@dataclass(frozen=True)
class LinkEnd:
    """One link of the unit being placed: its interface index and the other end
    (an already placed unit's interface in world coordinates)."""

    iface: int
    other: Interface
    outgoing: bool  # the unit's interface is the link's source (output)


def link_cost(unit: Unit, origin: Tile, pose: Pose, ends: Iterable[LinkEnd], costs: PlaceCosts):
    total = 0.0
    for e in ends:
        mine = unit.interfaces[e.iface].world(origin, pose)
        if mine.kind != "item":
            dx, dy = mine.tile.x - e.other.tile.x, mine.tile.y - e.other.tile.y
            total += costs.step * (abs(dx) + abs(dy))
            continue
        src, dst = (mine, e.other) if e.outgoing else (e.other, mine)
        total += estimate(*link_ends(src, dst), costs)
    return total


@dataclass(frozen=True)
class _Posed:
    pose: Pose
    tiles: frozenset[Tile]  # footprint at origin (0, 0)
    need: frozenset[Tile]  # footprint + margin + port tiles


def _posed(unit: Unit, gd: GameData, pose: Pose, margin: int) -> _Posed:
    w = place_unit(unit, Tile(0, 0), pose)
    tiles = w.layout.tiles(gd)
    need: set[Tile] = set()
    for t in tiles:
        for dx in range(-margin, margin + 1):
            for dy in range(-margin, margin + 1):
                need.add(Tile(t.x + dx, t.y + dy))
    for i in w.interfaces:
        need.update(port_tiles(i))
    return _Posed(pose, tiles, frozenset(need))


def candidates(
    unit: Unit,
    gd: GameData,
    free: Callable[[Tile], bool],
    terrain: Terrain,
    ends: list[LinkEnd],
    *,
    poses: Iterable[Pose] = (Pose(),),
    margin: int = 1,
    costs: PlaceCosts | None = None,
    reach: int | None = None,
) -> Iterator[tuple[float, Tile, Pose]]:
    """Fitting placements of ``unit`` in increasing cost (link estimate + resource
    penalty), as ``(cost, origin, pose)``. The window is the bounding box of the other
    ends' ports grown by ``reach`` (default: the unit's longer side + ``costs.slack``);
    the resource penalty is only computed for fitting candidates, and the scan stops as
    soon as the next link estimate alone exceeds the best total found (the penalty is
    never negative)."""
    costs = costs or PlaceCosts()
    if not ends:
        raise ValueError("link-driven placement needs at least one link to a placed unit")
    posed = [_posed(unit, gd, p, margin) for p in poses]
    w, h = unit.layout.size(gd)
    r = reach if reach is not None else max(w, h) + costs.slack
    xs = [e.other.tile.x for e in ends]
    ys = [e.other.tile.y for e in ends]
    cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
    lo, hi = Tile(min(xs) - r, min(ys) - r), Tile(max(xs) + r, max(ys) + r)
    scored: list[tuple[float, int, int, Tile, Pose]] = []
    for k, ps in enumerate(posed):
        for oy in range(lo.y, hi.y + 1):
            for ox in range(lo.x, hi.x + 1):
                o = Tile(ox, oy)
                c = link_cost(unit, o, ps.pose, ends, costs)
                scored.append((c, abs(ox - cx) + abs(oy - cy), k, o, ps.pose))
    scored.sort(key=lambda s: (s[0], s[1], s[2], s[3].y, s[3].x))
    found: list[tuple[float, Tile, Pose]] = []
    for c, _, k, o, pose in scored:
        if found and c > found[0][0]:
            found.sort(key=lambda f: f[0])
            while found and found[0][0] <= c:
                yield found.pop(0)
        ps = posed[k]
        if not all(free(Tile(t.x + o.x, t.y + o.y)) for t in ps.need):
            continue
        ore = sum(1 for t in ps.tiles if terrain.resource_at(Tile(t.x + o.x, t.y + o.y)))
        found.append((c + costs.resource * ore, o, pose))
    yield from sorted(found, key=lambda f: f[0])


def place_linked(
    name: str,
    unit: Unit,
    gd: GameData,
    free: Callable[[Tile], bool],
    terrain: Terrain,
    ends: list[LinkEnd],
    *,
    skip: int = 0,
    **kw: Any,
) -> PlacedUnit:
    """The cheapest fitting placement (``skip``-th cheapest when retrying after a routing
    failure); grows the window once before giving up."""
    for grow in (1, 2):
        costs = kw.get("costs") or PlaceCosts()
        w, h = unit.layout.size(gd)
        reach = kw.get("reach")
        reach = (reach if reach is not None else max(w, h) + costs.slack) * grow
        found = candidates(unit, gd, free, terrain, ends, **{**kw, "reach": reach})
        for n, (_, origin, pose) in enumerate(found):
            if n == skip:
                return PlacedUnit(name, unit, origin, pose)
    raise ValueError(f"no room for {name} within reach of its links")


__all__ = [
    "LinkEnd",
    "PlaceCosts",
    "candidates",
    "estimate",
    "find_spot",
    "link_ends",
    "place_linked",
    "place_port",
    "pose_for",
    "rings",
]
