"""Routing grid (plan-phase3a §3.4).

A :class:`RouteGrid` is what the routers see of a site: which tiles are free to build
on, what each occupied tile holds, and a few derived maps:

- ``reserved``: tiles kept clear for an interface (the ``k`` tiles in front of every
  item/fluid port, side-load approach tiles); only links touching that interface may
  use them;
- ``feeders``: tile -> tiles of belt-like entities that push items into it (belts,
  underground exits, splitters, output loaders). A route belt must not receive items
  from anything but its own predecessor, otherwise it would curve or be side-loaded;
- ``item_hazards``: tiles an inserter picks from / drops to or a drill drops onto;
  a route belt there would gain or lose items;
- ``fluid_ports``: (tile, outward direction) of every normal fluid connection, with its
  fluid; a new pipe next to a foreign port would join the two systems;
- underground spans: tiles strictly between the two ends of an existing underground
  belt / pipe-to-ground pair, per (entity name, axis). A new end inside a span, or a
  span with another end of the same name and axis inside it, would steal the pairing.

Everything is a pure function of the terrain snapshot and the placed layouts; routers
call :meth:`RouteGrid.add` after each link so later links see earlier ones.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from fgen.data.schema import GameData
from fgen.geom import fluid, footprint, inserter, mining
from fgen.geom.vec import CARDINALS, Direction, Tile
from fgen.layout.model import Layout, Placement
from fgen.terrain.model import Area, Terrain

BELT_LIKE = frozenset({"transport-belt", "underground-belt", "splitter", "loader", "loader-1x1"})


def axis_of(d: Direction) -> str:
    return "x" if d in (Direction.E, Direction.W) else "y"


@dataclass
class Costs:
    step: float = 1.0
    turn: float = 0.5
    resource: float = 2.0  # extra per tile on ore (keeps routes off minable ground)
    underground: float = 2.0  # extra per underground pair over its span


@dataclass
class RouteGrid:
    gd: GameData
    terrain: Terrain
    bounds: Area
    costs: Costs = field(default_factory=Costs)
    occupant: dict[Tile, Placement] = field(default_factory=dict)
    owner: dict[Tile, str] = field(default_factory=dict)
    reserved: dict[Tile, set[str]] = field(default_factory=dict)
    feeders: dict[Tile, set[Tile]] = field(default_factory=dict)
    item_hazards: set[Tile] = field(default_factory=set)
    fluid_ports: dict[tuple[Tile, Direction], str | None] = field(default_factory=dict)
    spans: dict[tuple[str, str], set[Tile]] = field(default_factory=dict)
    blocked: set[Tile] = field(default_factory=set)

    # -- construction -----------------------------------------------------------------
    @classmethod
    def build(
        cls,
        gd: GameData,
        terrain: Terrain,
        layouts: Iterable[tuple[str, Layout, Mapping[int, str]]],
        *,
        bounds: Area | None = None,
        costs: Costs | None = None,
    ) -> RouteGrid:
        """``layouts``: (owner name, world layout, placement index -> fluid)."""
        g = cls(gd, terrain, bounds or terrain.bounds(), costs or Costs())
        for owner, lay, fluids in layouts:
            g.add(lay.placements, owner, fluids)
        return g

    def add(
        self,
        placements: Iterable[Placement],
        owner: str,
        fluids: Mapping[int, str] | None = None,
        fluid_all: str | None = None,
    ) -> None:
        """Mark placements as built (``fluid_all`` gives every fluid entity that fluid)."""
        gd = self.gd
        ps = list(placements)
        for k, p in enumerate(ps):
            e = gd.entity(p.name)
            ts = footprint.tiles_of(gd, p.name, p.position, p.direction)
            for t in ts:
                self.occupant[t] = p
                self.owner[t] = owner
            d = p.direction
            if not d.is_cardinal:
                continue
            if e.type == "transport-belt" or (
                e.type == "underground-belt" and p.extra.get("type", "input") == "output"
            ):
                (t,) = ts
                self.feeders.setdefault(t.neighbor(d), set()).add(t)
            elif e.type == "splitter" or (
                e.type in ("loader", "loader-1x1") and p.extra.get("type") == "output"
            ):
                front = _front_tiles(ts, d)
                for t in front:
                    self.feeders.setdefault(t.neighbor(d), set()).add(t)
            elif e.type == "inserter":
                self.item_hazards.add(inserter.pickup_tile(gd, p.name, p.position, d))
                self.item_hazards.add(inserter.drop_tile(gd, p.name, p.position, d))
            elif e.type == "mining-drill" and e.vector_to_place_result is not None:
                self.item_hazards.add(mining.drop_tile(gd, p.name, p.position, d))
            if e.fluid_box is not None or e.output_fluid_box is not None:
                f = fluid_all if fluid_all is not None else (fluids or {}).get(k)
                for t, pd in fluid.connection_points(gd, p.name, p.position, d):
                    self.fluid_ports[(t, pd)] = f
        self._pair_spans(ps)

    def _pair_spans(self, ps: list[Placement]) -> None:
        gd = self.gd
        for p in ps:
            e = gd.entity(p.name)
            if not p.direction.is_cardinal:
                continue
            if e.type == "underground-belt" and p.extra.get("type", "input") == "input":
                reach, step = int(e.max_distance or 0), p.direction
                want = "output"
            elif e.type == "pipe-to-ground":
                # the underground connection points opposite the normal one
                reach = int(e.max_underground_distance or 0)
                step = p.direction.opposite
                want = None
            else:
                continue
            (t,) = footprint.tiles_of(gd, p.name, p.position, p.direction)
            between: list[Tile] = []
            for k in range(1, reach + 1):
                n = t.neighbor(step, k)
                q = self.occupant.get(n)
                if q is not None and q.name == p.name and axis_of(q.direction) == axis_of(step):
                    ok = (
                        q.extra.get("type", "input") == want
                        if want is not None
                        else q.direction == p.direction.opposite
                    )
                    if ok:
                        self.spans.setdefault((p.name, axis_of(step)), set()).update(between)
                    break
                between.append(n)

    # -- queries -------------------------------------------------------------------
    def in_bounds(self, t: Tile) -> bool:
        a, b = self.bounds
        return a.x <= t.x <= b.x and a.y <= t.y <= b.y

    def free(self, t: Tile, allowed: frozenset[str] = frozenset()) -> bool:
        """Buildable, unoccupied, inside the bounds, not reserved for someone else."""
        if t in self.occupant or t in self.blocked or not self.in_bounds(t):
            return False
        if not self.terrain.can_build(t):
            return False
        r = self.reserved.get(t)
        return not r or bool(r & allowed)

    def tile_cost(self, t: Tile) -> float:
        return self.costs.step + (self.costs.resource if t in self.terrain.resources else 0.0)

    def reserve(self, tiles: Iterable[Tile], owner: str) -> None:
        for t in tiles:
            self.reserved.setdefault(t, set()).add(owner)

    def foreign_feeders(self, t: Tile, own_pred: Tile | None) -> bool:
        return any(f != own_pred for f in self.feeders.get(t, ()))

    def in_span(self, name: str, d: Direction, t: Tile) -> bool:
        return t in self.spans.get((name, axis_of(d)), ())

    def same_line_end(self, name: str, d: Direction, t: Tile) -> bool:
        """``t`` holds an underground of ``name`` on the axis of ``d``."""
        q = self.occupant.get(t)
        return q is not None and q.name == name and axis_of(q.direction) == axis_of(d)

    def foreign_port_facing(self, t: Tile, own: frozenset[tuple[Tile, Direction]]) -> bool:
        """Some fluid port outside ``own`` points at ``t`` from a neighbour."""
        for d in CARDINALS:
            n = t.neighbor(d)
            key = (n, d.opposite)
            if key in self.fluid_ports and key not in own:
                return True
        return False


def _front_tiles(ts: frozenset[Tile], d: Direction) -> list[Tile]:
    off = d.tile_offset()
    best = max(t.x * off.x + t.y * off.y for t in ts)
    return [t for t in ts if t.x * off.x + t.y * off.y == best]
