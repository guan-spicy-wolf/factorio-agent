"""Build a site from units and links without hand-written coordinates (plan-phase7 task A).

A :class:`SitePlan` names the anchored units (already placed: mining units carry world
coordinates), the free units (generated units and fixtures) and the links. ``build``
places the free units one at a time with :func:`fgen.site.place.place_linked`:

- order: flow order. Repeatedly take, among the unplaced units that have at least one
  link to a placed unit and whose producers (other than fixtures) are all placed, the
  one that is not a fixture, then has the most placed neighbours, then the largest
  linked rate (big flows get the short belts); a unit with several producers is thus
  placed after all of them, between them. When nothing is placed yet (a site without
  anchors) the largest non-fixture unit sits at the site origin facing north;
- position: the cheapest fitting candidate of the link estimate (belt length, turns,
  detours, footprint on resources), see :mod:`fgen.site.place`.

``plan_and_connect`` routes the result with :func:`fgen.route.connect.connect`; when a
link cannot be routed it moves the unit at that link's placed-last end to its next
candidate and tries again, a bounded number of times. The record of what was tried is
returned with the connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fgen.data.schema import GameData
from fgen.geom.vec import CARDINALS, Tile
from fgen.layout.model import PlacedUnit, Pose, Unit
from fgen.route import belt as rbelt
from fgen.route.connect import Connection, connect
from fgen.site.model import Link, Site, SiteError
from fgen.site.place import LinkEnd, PlaceCosts, place_linked, port_tiles
from fgen.terrain.model import Terrain


@dataclass
class SitePlan:
    terrain: Terrain
    fixed: list[PlacedUnit] = field(default_factory=list)  # anchored, world coordinates
    free: list[tuple[str, Unit]] = field(default_factory=list)  # to be placed, in any order
    links: list[Link] = field(default_factory=list)
    costs: PlaceCosts = field(default_factory=PlaceCosts)
    # placement order priorities among the ready units (a permutation of ORDER_KEYS):
    # generated unit before fixture, most placed neighbours, largest linked rate
    order_priority: tuple[str, ...] = ("unit", "placed_neighbours", "linked_rate")
    margin: int = 1  # free tiles around every placed unit
    reach: int | None = None  # placement window radius (None: unit size + slack)
    poses: tuple[Pose, ...] = tuple(Pose(d) for d in CARDINALS)  # candidate rotations


def free_fn(terrain: Terrain, units: list[PlacedUnit], gd: GameData):  # -> Callable
    """Buildable and not covered by a placed unit nor by the tiles its ports keep clear."""
    taken: set[Tile] = set()
    for u in units:
        w = u.world()
        taken |= w.layout.tiles(gd)
        for i in w.interfaces:
            taken.update(port_tiles(i))

    def free(t: Tile) -> bool:
        return terrain.can_build(t) and t not in taken

    return free


def _is_fixture(unit: Unit) -> bool:
    return "fixture" in unit.meta


def _linked_rate(unit: Unit, name: str, links: list[Link]) -> float:
    total = 0.0
    for ln in links:
        for ep in (ln.src, ln.dst):
            if ep.unit == name:
                i = unit.interfaces[ep.iface]
                total += i.fixed_rate() or i.min_rate() or 0.0
    return total


def ends_of(
    name: str, unit: Unit, links: list[Link], placed: dict[str, PlacedUnit]
) -> list[LinkEnd]:
    """The links of ``name`` whose other end is already placed, as placement inputs."""
    out: list[LinkEnd] = []
    for ln in links:
        if ln.src.unit == name and ln.dst.unit in placed:
            other = placed[ln.dst.unit].world().interfaces[ln.dst.iface]
            out.append(LinkEnd(ln.src.iface, other, True))
        elif ln.dst.unit == name and ln.src.unit in placed:
            other = placed[ln.src.unit].world().interfaces[ln.src.iface]
            out.append(LinkEnd(ln.dst.iface, other, False))
    return out


def order(plan: SitePlan, gd: GameData) -> list[str]:
    """Placement order of the free units (see the module docstring)."""
    placed = {u.name for u in plan.fixed}
    left = dict(plan.free)
    fixtures = {n for n, u in left.items() if _is_fixture(u)}
    out: list[str] = []
    while left:
        ready = []
        for name, unit in left.items():
            nbrs = {
                ln.dst.unit if ln.src.unit == name else ln.src.unit
                for ln in plan.links
                if name in (ln.src.unit, ln.dst.unit)
            }
            producers = {ln.src.unit for ln in plan.links if ln.dst.unit == name} - fixtures
            n = len(nbrs & placed)
            if n and producers <= placed:  # flow order: a unit follows all its producers
                score = {
                    "unit": not _is_fixture(unit),
                    "placed_neighbours": n,
                    "linked_rate": _linked_rate(unit, name, plan.links),
                }
                ready.append((tuple(score[k] for k in plan.order_priority), name))
        if ready:
            ready.sort(key=lambda r: r[0], reverse=True)
            pick = ready[0][1]
        else:  # nothing placed yet: the largest non-fixture unit starts the site
            pick = max(
                left,
                key=lambda n: (not _is_fixture(left[n]), len(left[n].layout.tiles(gd)), n),
            )
        out.append(pick)
        placed.add(pick)
        del left[pick]
    return out


def build(plan: SitePlan, gd: GameData, skip: dict[str, int] | None = None) -> Site:
    """Place every free unit (``skip[name]``: take that unit's n-th cheapest candidate)."""
    skip = skip or {}
    placed: dict[str, PlacedUnit] = {u.name: u for u in plan.fixed}
    units: list[PlacedUnit] = list(plan.fixed)
    free_units = dict(plan.free)
    for name in order(plan, gd):
        unit = free_units[name]
        ends = ends_of(name, unit, plan.links, placed)
        if ends:
            pu = place_linked(
                name,
                unit,
                gd,
                free_fn(plan.terrain, units, gd),
                plan.terrain,
                ends,
                skip=skip.get(name, 0),
                poses=plan.poses,
                margin=plan.margin,
                costs=plan.costs,
                reach=plan.reach,
            )
        elif not units:
            pu = PlacedUnit(name, unit, Tile(0, 0))
        else:
            raise SiteError(f"{name} has no link to any placed unit")
        placed[name] = pu
        units.append(pu)
    return Site(plan.terrain, units, plan.links)


def plan_and_connect(
    plan: SitePlan, gd: GameData, *, attempts: int = 6
) -> tuple[Site, Connection, list[dict[str, Any]]]:
    """Build, route; on a routing failure re-place the failing link's later-placed unit
    at its next candidate. Returns the site, the connection and the attempt log."""
    skip: dict[str, int] = {}
    seq = order(plan, gd)
    log: list[dict[str, Any]] = []
    last: Exception | None = None
    for _ in range(attempts):
        site = build(plan, gd, skip)
        try:
            conn = connect(site, gd)
        except rbelt.RouteError as exc:
            last = exc
            ln = next((x for x in plan.links if x.label == exc.link), None)
            who = None
            if ln is not None:
                cands = [n for n in (ln.src.unit, ln.dst.unit) if n in seq]
                who = max(cands, key=seq.index) if cands else None
            log.append({"skip": dict(skip), "error": str(exc), "moved": who})
            if who is None:
                break
            skip[who] = skip.get(who, 0) + 1
            continue
        log.append({"skip": dict(skip), "error": None, "moved": None})
        return site, conn, log
    assert last is not None
    raise last


ORDER_KEYS = SitePlan.order_priority

__all__ = ["ORDER_KEYS", "SitePlan", "build", "ends_of", "free_fn", "order", "plan_and_connect"]
