"""Connect a ``Unit``'s interfaces to infinite sources, counting sinks and free power
(plan §3.4). Everything is placed with ``create_entity`` and recorded, so the
harness can be excluded from ``read_back``.

Per interface kind (``d`` = interface direction, i.e. the belt flow):

- **item input**, ``lane`` ``both``: the belt is extended upstream and fed head-on by an
  ``infinity-chest`` -> loader (loader tier matched to the belt; loaders keep up with
  their belt, ``fgen verify`` #7).
- **item input**, ``lane`` ``left`` / ``right``: the belt is extended upstream; a
  one-tile feeder belt side-loads the extension from the lane's side (side-loading fills
  the near lane, ``fgen verify`` #4), itself fed head-on by chest -> loader. A straight
  extension tile stays behind the side-load tile so it stays a side-load, not a curve.
  Two lane interfaces on the same tile (ore left, fuel right) share the side-load tile.
- **item output**: the belt is extended downstream into a loader (``type=input``) and a
  ``steel-chest``; an in-game per-tick handler counts and empties the chest.
- **power**: an ``electric-energy-interface`` and a pole of the unit's pole type,
  placed outside the unit within wire reach of the interface pole.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fgen.data.schema import GameData
from fgen.geom import belt as beltgeom
from fgen.geom import footprint, pole
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout.model import Interface, Unit
from fgen.sim import lua
from fgen.sim.rcon import RconClient

SOURCE_CHEST = "infinity-chest"
SINK_CHEST = "steel-chest"
POWER_SOURCE = "electric-energy-interface"
LOADER_FOR_BELT = {
    "transport-belt": "loader",
    "fast-transport-belt": "fast-loader",
    "express-transport-belt": "express-loader",
}
UPSTREAM = 3  # extension tiles before an input interface (side-load on the 2nd)
DOWNSTREAM = 2  # extension tiles after an output interface
SOURCE_STOCK = 200  # items an infinity chest keeps (refilled every tick by the game)


class HarnessError(RuntimeError):
    pass


@dataclass
class Harness:
    specs: list[dict[str, Any]] = field(default_factory=list)  # create_entity tables
    sources: list[tuple[int, str]] = field(default_factory=list)  # (spec index, item)
    sinks: list[tuple[int, str]] = field(default_factory=list)  # (spec index, item)
    tiles: set[Tile] = field(default_factory=set)
    unit_numbers: list[int] = field(default_factory=list)
    power_pole: int | None = None  # spec index


def _belt_at(unit: Unit, gd: GameData, t: Tile) -> str:
    for i in unit.layout.tile_index(gd).get(t, []):
        name = unit.layout.placements[i].name
        if gd.entity(name).type in beltgeom.BELT_TYPES:
            return name
    raise HarnessError(f"no belt at interface tile {t}")


def _plain_belt(gd: GameData, name: str) -> str:
    """The transport-belt of the same tier as ``name`` (undergrounds/splitters)."""
    if gd.entity(name).type == "transport-belt":
        return name
    speed = gd.entity(name).speed
    for n, e in gd.entities.items():
        if e.type == "transport-belt" and e.speed == speed:
            return n
    raise HarnessError(f"no transport-belt with the speed of {name}")


class _Builder:
    def __init__(self, unit: Unit, gd: GameData, origin: Tile) -> None:
        self.unit, self.gd, self.origin = unit, gd, origin
        self.blocked: set[Tile] = {t + origin for t in unit.layout.tiles(gd)}
        self.h = Harness()

    def world(self, t: Tile) -> Tile:
        return t + self.origin

    def add(self, name: str, anchor_center: Vec2, d: Direction = Direction.N, **kw: Any) -> int:
        ts = footprint.tiles_of(self.gd, name, anchor_center, d)
        clash = ts & (self.blocked | self.h.tiles)
        if clash:
            raise HarnessError(f"harness {name} at {anchor_center} overlaps {sorted(clash)[:3]}")
        self.h.tiles |= ts
        spec = {"name": name, "position": [anchor_center.x, anchor_center.y], "direction": int(d)}
        spec.update(kw)
        self.h.specs.append(spec)
        return len(self.h.specs) - 1

    def belt(self, name: str, t: Tile, d: Direction) -> int:
        return self.add(name, t.center(), d)

    def feed_head_on(self, belt: str, first: Tile, d: Direction, item: str) -> None:
        """Chest -> loader -> belt tile ``first`` (flow ``d``), loader right behind it."""
        loader = LOADER_FOR_BELT.get(belt)
        if loader is None or loader not in self.gd.entities:
            raise HarnessError(f"no loader for {belt}")
        c = first.center()
        back = d.opposite.vector()
        self.add(loader, c + back * 1.5, d, type="output")
        i = self.add(SOURCE_CHEST, c + back * 3, Direction.N)
        self.h.sources.append((i, item))

    def item_input(self, ifaces: list[Interface]) -> None:
        t0 = self.world(ifaces[0].tile)
        d = ifaces[0].direction
        belt = _plain_belt(self.gd, _belt_at(self.unit, self.gd, ifaces[0].tile))
        up = d.opposite
        ext = [t0.neighbor(up, k) for k in range(1, UPSTREAM + 1)]
        for t in ext:
            self.belt(belt, t, d)
        if any(i.lane in (None, "both") for i in ifaces):
            if len(ifaces) > 1:
                raise HarnessError("a 'both' lane input cannot share its tile")
            # feed the last extension tile from behind
            self.feed_head_on(belt, ext[-1], d, str(ifaces[0].material))
            return
        side_tile = ext[1]
        for i in ifaces:
            side = beltgeom.lane_side(d, i.lane)  # type: ignore[arg-type]
            feeder = side_tile.neighbor(side)
            flow = side.opposite  # towards the main belt
            self.belt(belt, feeder, flow)
            self.feed_head_on(belt, feeder, flow, str(i.material))

    def item_output(self, i: Interface) -> None:
        t0 = self.world(i.tile)
        d = i.direction
        belt = _plain_belt(self.gd, _belt_at(self.unit, self.gd, i.tile))
        ext = [t0.neighbor(d, k) for k in range(1, DOWNSTREAM + 1)]
        for t in ext:
            self.belt(belt, t, d)
        c = ext[-1].center()
        fwd = d.vector()
        self.add(LOADER_FOR_BELT[belt], c + fwd * 1.5, d, type="input")
        k = self.add(SINK_CHEST, c + fwd * 3, Direction.N)
        self.h.sinks.append((k, str(i.material)))

    def power(self, i: Interface) -> None:
        unit_pole = next(
            (
                p
                for p in self.unit.layout.placements
                if self.gd.entity(p.name).type == "electric-pole" and p.position.floor() == i.tile
            ),
            None,
        )
        if unit_pole is None:
            raise HarnessError(f"no pole at power interface {i.tile}")
        name = unit_pole.name
        target = unit_pole.position + Vec2(self.origin.x, self.origin.y)
        reach = int(pole.wire_distance(self.gd, name, name))
        best: tuple[float, Tile, Tile] | None = None
        t0 = target.floor()
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                a = Tile(t0.x + dx, t0.y + dy)
                pc = footprint.center_for_tile_anchor(self.gd, name, a)
                if not pole.wire_reaches(self.gd, name, pc, name, target):
                    continue
                pts = footprint.tiles_of(self.gd, name, pc, Direction.N)
                if pts & (self.blocked | self.h.tiles):
                    continue
                sup = pole.supply_tiles(self.gd, name, pc)
                for e in sorted(sup):
                    ec = footprint.center_for_tile_anchor(self.gd, POWER_SOURCE, e)
                    ets = footprint.tiles_of(self.gd, POWER_SOURCE, ec, Direction.N)
                    if ets & (self.blocked | self.h.tiles | pts):
                        continue
                    # prefer the farthest spot still in reach: keeps clear of the unit
                    score = pc.distance(target)
                    if best is None or score > best[0]:
                        best = (score, a, e)
                    break
        if best is None:
            raise HarnessError("no room for the harness pole and power source")
        _, a, e = best
        self.h.power_pole = self.add(name, footprint.center_for_tile_anchor(self.gd, name, a))
        self.add(POWER_SOURCE, footprint.center_for_tile_anchor(self.gd, POWER_SOURCE, e))


def plan(unit: Unit, gd: GameData, origin: Tile) -> Harness:
    """Harness entities (world coordinates) for ``unit`` placed at ``origin``."""
    b = _Builder(unit, gd, origin)
    inputs: dict[Tile, list[Interface]] = {}
    for i in unit.interfaces:
        if i.kind == "item" and i.role == "input":
            if i.material is None:  # e.g. a fuel lane an electric furnace leaves unused
                continue
            inputs.setdefault(i.tile, []).append(i)
    for group in inputs.values():
        b.item_input(group)
    for i in unit.interfaces:
        if i.kind == "item" and i.role == "output":
            b.item_output(i)
    for i in unit.interfaces:
        if i.kind == "power" and i.role == "input":
            b.power(i)
            break
    return b.h


def build(rcon: RconClient, h: Harness) -> Harness:
    """Create the harness entities, configure sources, register them under
    ``storage.fgen_harness``. Fills ``h.unit_numbers``."""
    code = (
        lua.PRELUDE
        + f"local specs = {lua.lit(h.specs)}\n"
        + f"local sources = {lua.lit([[i + 1, item] for i, item in h.sources])}\n"
        + f"local sinks = {lua.lit([[i + 1, item] for i, item in h.sinks])}\n"
        + f"local pole_i = {lua.lit(None if h.power_pole is None else h.power_pole + 1)}\n"
        + f"""
local ents, out = {{}}, {{unit_numbers={{}}, failed={{}}}}
for i, sp in ipairs(specs) do
  sp.force = F
  local e = S.create_entity(sp)
  ents[i] = e
  if e then table.insert(out.unit_numbers, e.unit_number) else table.insert(out.failed, i) end
end
for _, s in ipairs(sources) do
  local e = ents[s[1]]
  if e then
    e.infinity_container_filters = {{{{index=1, name=s[2], count={SOURCE_STOCK},
                                      mode='exactly'}}}}
  end
end
storage.fgen_harness = {{sinks={{}}, pole=pole_i and ents[pole_i] or nil}}
for k, s in ipairs(sinks) do
  storage.fgen_harness.sinks[k] = {{entity=ents[s[1]], item=s[2], total=0, first_tick=nil}}
end
out.pole_net = pole_i and ents[pole_i] and ents[pole_i].electric_network_id or nil
"""
        + lua.emit("out")
    )
    out = rcon.lua_json(code)
    failed = out["failed"]
    if failed:
        raise HarnessError(f"create_entity failed for harness specs {failed}")
    h.unit_numbers = [int(u) for u in (out["unit_numbers"] or [])]
    return h
