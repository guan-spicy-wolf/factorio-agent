"""Test fixtures as units, and the harness that wires them to a unit (plan-phase3a §3.3).

A fixture is a tiny :class:`~fgen.layout.model.Unit` with its own interfaces, so the
router treats "fixture" and "unit" alike: a fixture is just the other end of a
contract. Every fixture is built facing east in its own frame (``layout.transform``
turns it):

- :func:`source_unit`: ``infinity-chest`` -> loader (``type=output``) -> one belt; output
  interface on the belt, lane ``both``, ``rate`` None (supplies whatever the belt takes).
  Loader tier matches the belt (loaders keep up with their belt, ``fgen verify`` #7).
- :func:`sink_unit`: one belt -> loader (``type=input``) -> ``steel-chest``; input
  interface on the belt; an in-game per-tick handler counts and empties the chest.
- :func:`power_unit`: ``electric-energy-interface`` + one pole in its supply area; power
  interface on the pole.
- :func:`fluid_source_unit`: one ``infinity-pipe`` (filter set after building:
  ``set_infinity_pipe_filter{name, percentage, mode}``, checked on 2.1.23).
- :func:`fluid_sink_unit`: a ``storage-tank``; read with ``LuaEntity.get_fluid(1)``
  (2.1 has no ``LuaEntity.fluidbox``; VERIFY §10).

:func:`plan` (used by :class:`fgen.sim.measure.RconCharacterizer`) builds a site from a
unit and one fixture per interface, routes it with :func:`fgen.route.connect.connect`
and returns the fixture and route entities as ``create_entity`` specs. Lane inputs get
their own source each and are side-loaded onto a stub (ore left, coal right), exactly
what the router does for any producer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fgen.data.schema import GameData
from fgen.geom import belt as beltgeom
from fgen.geom import fluid as fluidgeom
from fgen.geom import footprint, pole
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import (
    Interface,
    Layout,
    PlacedUnit,
    Placement,
    PortGroup,
    PortSpace,
    RateProfile,
    Unit,
)
from fgen.sim import lua
from fgen.sim.rcon import RconClient
from fgen.site import place
from fgen.site.model import Endpoint, Link, Site
from fgen.terrain.model import Terrain, area_tiles

SOURCE_CHEST = "infinity-chest"
SINK_CHEST = "steel-chest"
POWER_SOURCE = "electric-energy-interface"
FLUID_SOURCE = "infinity-pipe"
FLUID_SINK = "storage-tank"
LOADER_FOR_BELT = {
    "transport-belt": "loader",
    "fast-transport-belt": "fast-loader",
    "express-transport-belt": "express-loader",
}
SOURCE_STOCK = 200  # items an infinity chest keeps (refilled every tick by the game)
FIXTURE_GAP = 3  # source/sink ports sit this many tiles from the port they serve
FIXTURE_GROUP = "belt"  # port group of a source / sink fixture (its one belt)
E = Direction.E


class HarnessError(RuntimeError):
    pass


# ---- fixtures as units ---------------------------------------------------------------


def _loader(gd: GameData, belt: str) -> str:
    name = LOADER_FOR_BELT.get(beltgeom_plain(gd, belt))
    if name is None or name not in gd.entities:
        raise HarnessError(f"no loader for {belt}")
    return name


def beltgeom_plain(gd: GameData, belt: str) -> str:
    from fgen.route.belt import plain_belt

    return plain_belt(gd, belt)


def _put(gd: GameData, name: str, anchor: Tile, d: Direction = E, **extra: Any) -> Placement:
    return Placement(name, footprint.center_for_tile_anchor(gd, name, anchor, d), d, dict(extra))


def source_unit(gd: GameData, item: str, belt: str = "transport-belt") -> Unit:
    belt = beltgeom_plain(gd, belt)
    loader = _loader(gd, belt)
    lw, _ = footprint.size(gd, loader, E)
    cw, _ = footprint.size(gd, SOURCE_CHEST)
    ps = [
        _put(gd, SOURCE_CHEST, Tile(0, 0), Direction.N),
        _put(gd, loader, Tile(cw, 0), E, type="output"),
        _put(gd, belt, Tile(cw + lw, 0), E),
    ]
    out = Interface(
        "item",
        item,
        Tile(cw + lw, 0),
        E,
        "both",
        None,
        "output",
        ["infinite source: supplies whatever the belt takes, both lanes"],
        rate_profile=_belt_on_demand(gd, belt),
        entity=belt,
        port_group=FIXTURE_GROUP,
        space=PortSpace.item("direct"),
    )
    meta = {"fixture": {"kind": "source", "item": item, "chest": 0}, "family": "fixture/source"}
    return Unit(Layout(ps), [out], meta, (PortGroup(FIXTURE_GROUP),))


def _belt_on_demand(gd: GameData, belt: str) -> RateProfile:
    """Whatever the fixture's belt carries: both lanes, each up to its lane capacity."""
    from fgen.geom import belt as bg

    lane = bg.lane_items_per_second(gd, belt)
    return RateProfile.on_demand(bg.items_per_second(gd, belt), {"left": lane, "right": lane})


def sink_unit(gd: GameData, item: str | None, belt: str = "transport-belt") -> Unit:
    belt = beltgeom_plain(gd, belt)
    loader = _loader(gd, belt)
    lw, _ = footprint.size(gd, loader, E)
    ps = [
        _put(gd, belt, Tile(0, 0), E),
        _put(gd, loader, Tile(1, 0), E, type="input"),
        _put(gd, SINK_CHEST, Tile(1 + lw, 0), Direction.N),
    ]
    inp = Interface(
        "item",
        item,
        Tile(0, 0),
        E,
        "both",
        None,
        "input",
        ["counting sink: takes everything, both lanes"],
        rate_profile=_belt_on_demand(gd, belt),
        entity=belt,
        port_group=FIXTURE_GROUP,
        space=PortSpace.item("direct"),
    )
    meta = {
        "fixture": {"kind": "sink", "item": item, "chest": 2},
        "family": "fixture/sink",
        "belt_terminals": [Tile(0, 0)],
    }
    return Unit(Layout(ps), [inp], meta, (PortGroup(FIXTURE_GROUP),))


def power_unit(gd: GameData, pole_name: str = "small-electric-pole") -> Unit:
    ew, eh = footprint.size(gd, POWER_SOURCE)
    eei = _put(gd, POWER_SOURCE, Tile(0, 0), Direction.N)
    eei_tiles = eei.tiles(gd)
    for anchor in (Tile(ew, 0), Tile(0, eh), Tile(ew, eh)):
        c = footprint.center_for_tile_anchor(gd, pole_name, anchor)
        if pole.supply_tiles(gd, pole_name, c) & eei_tiles:
            p = Placement(pole_name, c)
            iface = Interface(
                "power",
                None,
                c.floor(),
                E,
                None,
                None,
                "output",
                ["infinite electricity"],
                entity=pole_name,
            )
            meta = {"fixture": {"kind": "power", "pole": 1}, "family": "fixture/power"}
            return Unit(Layout([eei, p]), [iface], meta)
    raise HarnessError(f"{pole_name} next to {POWER_SOURCE} does not supply it")


def fluid_source_unit(gd: GameData, fluid: str, percentage: float = 1.0) -> Unit:
    p = Placement(FLUID_SOURCE, Tile(0, 0).center())
    iface = Interface(
        "fluid",
        fluid,
        Tile(0, 0),
        E,
        None,
        None,
        "output",
        [f"infinite {fluid} (infinity-pipe)"],
        rate_profile=RateProfile.on_demand(),
        entity=FLUID_SOURCE,
    )
    meta = {
        "fixture": {"kind": "fluid-source", "fluid": fluid, "percentage": percentage},
        "family": "fixture/fluid-source",
        "fluids": {0: fluid},
    }
    return Unit(Layout([p]), [iface], meta)


def fluid_sink_unit(gd: GameData, fluid: str, tank: str = FLUID_SINK) -> Unit:
    c = footprint.center_for_tile_anchor(gd, tank, Tile(0, 0))
    ports = fluidgeom.connection_points(gd, tank, c, Direction.N)
    west = [(t, d) for t, d in ports if d == Direction.W]
    if not west:
        raise HarnessError(f"{tank} has no west-facing connection")
    t, d = west[0]
    iface = Interface(
        "fluid",
        fluid,
        t,
        d,
        None,
        None,
        "input",
        [f"{tank} collects {fluid}"],
        rate_profile=RateProfile.on_demand(),
        entity=tank,
    )
    # the fixture faces east like the others: its port points west (into the flow)
    meta = {
        "fixture": {"kind": "fluid-sink", "fluid": fluid},
        "family": "fixture/fluid-sink",
        "fluids": {0: fluid},
    }
    return Unit(Layout([Placement(tank, c)]), [iface], meta)


# ---- harness for one unit ------------------------------------------------------------


@dataclass
class Harness:
    specs: list[dict[str, Any]] = field(default_factory=list)  # create_entity tables
    sources: list[tuple[int, str]] = field(default_factory=list)  # (spec index, item)
    sinks: list[tuple[int, str]] = field(default_factory=list)  # (spec index, item)
    sink_units: list[str] = field(default_factory=list)  # fixture name per sink
    fluid_sources: list[tuple[int, str, float]] = field(default_factory=list)
    tiles: set[Tile] = field(default_factory=set)
    unit_numbers: list[int] = field(default_factory=list)
    power_pole: int | None = None  # spec index
    site: Site | None = None
    connection: Any = None


def spec_of(gd: GameData, p: Placement) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": p.name,
        "position": [p.position.x, p.position.y],
        "direction": int(p.direction),
    }
    t = gd.entity(p.name).type
    if t in ("underground-belt", "loader", "loader-1x1"):
        spec["type"] = p.extra.get("type", "input")
    return spec


def fixture_specs(
    site: Site, gd: GameData, conn: Any, units: list[str], *, routes: bool = True
) -> Harness:
    """Specs for the fixture units ``units`` and (with ``routes``) every routed entity
    of ``conn``."""
    h = Harness(site=site, connection=conn)
    for p in conn.layout.placements if routes else ():
        h.specs.append(spec_of(gd, p))
        h.tiles |= p.tiles(gd)
    for name in units:
        w = site.world(name)
        fx = w.meta["fixture"]
        base = len(h.specs)
        for p in w.layout.placements:
            h.specs.append(spec_of(gd, p))
            h.tiles |= p.tiles(gd)
        if fx["kind"] == "source":
            h.sources.append((base + fx["chest"], fx["item"]))
        elif fx["kind"] == "sink":
            h.sinks.append((base + fx["chest"], fx["item"] or ""))
            h.sink_units.append(name)
        elif fx["kind"] == "power":
            if h.power_pole is None:
                h.power_pole = base + fx["pole"]
        elif fx["kind"] == "fluid-source":
            h.fluid_sources.append((base, fx["fluid"], fx["percentage"]))
    return h


def _flat_terrain(unit: Unit, gd: GameData, origin: Tile, margin: int = 40) -> Terrain:
    lo, hi = unit.layout.bounds(gd)
    a = Tile(origin.x + lo.x - margin, origin.y + lo.y - margin)
    b = Tile(origin.x + hi.x + margin, origin.y + hi.y + margin)
    return Terrain(buildable=area_tiles((a, b)))


def attach_fixtures(
    site_units: list[PlacedUnit],
    target: str,
    gd: GameData,
    terrain: Terrain,
    *,
    skip: set[int] | None = None,
    side_from_upstream: bool = False,
) -> tuple[list[PlacedUnit], list[Link]]:
    """One fixture per interface of unit ``target`` (except indices in ``skip``),
    placed next to the port it serves (falling back to the nearest free spot). A
    side-loading feeder sits beside the approach tile, or with ``side_from_upstream``
    upstream of it on the approach row (its belt turns into the approach tile; used
    when a feeder beside it would cross the row of another port)."""
    skip = skip or set()
    placed = list(site_units)
    unit = next(u for u in placed if u.name == target)
    w = unit.world()
    taken: set[Tile] = set()
    for u in placed:
        taken |= u.world().layout.tiles(gd)

    def free(t: Tile) -> bool:
        return terrain.can_build(t) and t not in taken

    fixtures: list[PlacedUnit] = []
    links: list[Link] = []

    def add(pu: PlacedUnit) -> None:
        fixtures.append(pu)
        taken.update(pu.world().layout.tiles(gd))
        # keep the gap and port tiles of later fixtures clear of this one
        for i in pu.world().interfaces:
            taken.update(place.port_tiles(i, 0))

    for k, i in enumerate(w.interfaces):
        if k in skip:
            continue
        ep = Endpoint(target, k)
        fname = f"{target}.fx{k}"
        if i.kind == "item" and i.role == "input":
            if i.material is None:  # e.g. the fuel lane of an electric furnace
                continue
            fx = source_unit(gd, i.material, _port_belt(i))
            if i.lane in ("left", "right"):
                side = beltgeom.lane_side(i.direction, i.lane)  # type: ignore[arg-type]
                approach = i.tile.neighbor(i.direction.opposite).neighbor(side)
                if side_from_upstream:
                    want = approach.neighbor(i.direction.opposite, FIXTURE_GAP - 1)
                    pu = place.place_port(fname, fx, 0, want, i.direction)
                else:
                    want = approach.neighbor(side, FIXTURE_GAP - 1)
                    pu = place.place_port(fname, fx, 0, want, side.opposite)
            else:
                want = i.tile.neighbor(i.direction.opposite, FIXTURE_GAP)
                pu = place.place_port(fname, fx, 0, want, i.direction)
            if not place.footprint_ok(pu.world(), gd, free, margin=0, clearance=0):
                pu = place.find_spot(fname, fx, gd, free, want, poses=_poses(), clearance=1)
            add(pu)
            links.append(Link(Endpoint(fname, 0), ep))
        elif i.kind == "item" and i.role == "output":
            fx = sink_unit(gd, i.material, _port_belt(i))
            want = i.tile.neighbor(i.direction, FIXTURE_GAP)
            pu = place.place_port(fname, fx, 0, want, i.direction)
            if not place.footprint_ok(pu.world(), gd, free, margin=0, clearance=0):
                pu = place.find_spot(fname, fx, gd, free, want, poses=_poses(), clearance=1)
            add(pu)
            links.append(Link(ep, Endpoint(fname, 0)))
        elif i.kind == "power" and not any(
            u.unit.meta.get("fixture", {}).get("kind") == "power" for u in fixtures
        ):
            if i.entity is None:
                raise HarnessError(f"{target}#{k}: power interface names no pole")
            fx = power_unit(gd, i.entity)
            pu = place.find_spot(fname, fx, gd, free, i.tile, margin=1)
            add(pu)
            links.append(Link(Endpoint(fname, 0), ep))
        elif i.kind == "fluid":
            fx = (
                fluid_source_unit(gd, str(i.material))
                if i.role == "input"
                else fluid_sink_unit(gd, str(i.material))
            )
            want = i.tile.neighbor(i.direction, FIXTURE_GAP)
            pu = place.find_spot(fname, fx, gd, free, want, poses=_poses())
            add(pu)
            links.append(
                Link(Endpoint(fname, 0), ep) if i.role == "input" else Link(ep, Endpoint(fname, 0))
            )
    return fixtures, links


def _port_belt(i: Interface) -> str:
    """The belt a fixture at item port ``i`` uses: the port's own (contract ``entity``)."""
    if i.entity is None:
        raise HarnessError(f"item port at {i.tile} names no belt entity")
    return i.entity


def _poses():  # -> list[Pose]
    from fgen.layout.model import Pose

    return [Pose(d) for d in (Direction.N, Direction.E, Direction.S, Direction.W)]


def plan(unit: Unit, gd: GameData, origin: Tile, terrain: Terrain | None = None) -> Harness:
    """Fixtures and routes (world coordinates) for ``unit`` placed at ``origin``.
    Without ``terrain`` the surroundings are assumed flat and empty."""
    from fgen.route.connect import connect

    terrain = terrain or _flat_terrain(unit, gd, origin)
    main = PlacedUnit("unit", unit, origin)
    errors = []
    for upstream in (False, True):
        fixtures, links = attach_fixtures([main], "unit", gd, terrain, side_from_upstream=upstream)
        site = Site(terrain, [main, *fixtures], links)
        try:
            conn = connect(site, gd)
            break
        except Exception as exc:
            errors.append(str(exc))
    else:
        raise HarnessError(f"cannot wire the harness: {' | '.join(errors)}")
    bad = [v for v in conn.violations if v.code != "drill.no_ore"]
    if bad:
        raise HarnessError(f"harness violates invariants: {bad[:3]}")
    return fixture_specs(site, gd, conn, [f.name for f in fixtures])


def build(rcon: RconClient, h: Harness) -> Harness:
    """Create the harness entities, configure sources, register them under
    ``storage.fgen_harness``. Fills ``h.unit_numbers``."""
    code = (
        lua.PRELUDE
        + f"local specs = {lua.lit(h.specs)}\n"
        + f"local sources = {lua.lit([[i + 1, item] for i, item in h.sources])}\n"
        + f"local fsources = {lua.lit([[i + 1, f, p] for i, f, p in h.fluid_sources])}\n"
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
for _, s in ipairs(fsources) do
  local e = ents[s[1]]
  if e then e.set_infinity_pipe_filter{{name=s[2], percentage=s[3], mode='at-least'}} end
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
        idx = [int(i) - 1 for i in (failed.values() if isinstance(failed, dict) else failed)]
        raise HarnessError(
            f"create_entity failed for harness specs {[h.specs[i] for i in idx][:3]}"
        )
    h.unit_numbers = [int(u) for u in (out["unit_numbers"] or [])]
    return h
