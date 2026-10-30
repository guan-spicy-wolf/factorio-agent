"""Static invariant checks (plan §4.3). Returns violations; never raises.

Checked codes:

``entity.unknown``            placement name missing from the game data
``position.misaligned``       centre not on the tile grid for the entity's size
``overlap``                   two entities share a tile
``inserter.pickup_empty``     pickup tile holds no item-holding entity and is no interface
``inserter.drop_empty``       same for the drop tile
``power.uncovered``           electric consumer with no tile in any pole's supply area
``power.disconnected``        poles do not form one wire-connected network
``belt.discontinuous``        belt leads nowhere and is neither an output interface nor
                              a declared terminal
``underground.unpaired``      underground belt without a partner within ``max_distance``
``fluid.unintended_adjacency`` two touching fluid connections carry different fluids, or
                              an idle machine port (its box unused by the recipe) touches
                              a fluid connection
``fluid.box_unconnected``     a fluid box the machine's recipe uses has no port facing a
                              connection of its fluid (or a fluid interface tile)
``fluid.underground_unpaired`` a pipe-to-ground with no partner on its underground side
``drill.output_blocked``      a mining drill's output tile holds no belt or container and
                              is no item interface
``drill.no_ore``              no minable resource tile in a drill's search area (only
                              checked when ``resources`` is given)

Contract checks (``validate_unit`` only: they need the unit's port groups; phase 6a):

``contract.entity``           an item / power port names no entity, or not the belt-like
                              entity / pole that stands on its tile
``contract.space``            an item port without ``space``, or ``side`` approach on a
                              port that names no single lane
``contract.buffer``           an item port without ``buffer_items``, or one that disagrees
                              with the belt run behind the port tile (``belt_run``: tiles
                              x items per tile x the port's lane share)
``contract.rate``             ``rate`` disagrees with ``rate_profile`` (fixed: ``initial``;
                              on-demand: ``rate`` must be None), or per-lane rates do not
                              add up to ``initial`` / name a lane the port does not use
``contract.group``            ``port_group`` names no group of the unit, or one group's
                              ports are not on one tile and direction
``contract.fluid``            a fluid port names no entity with a connection on its tile
                              facing its direction, or lacks ``temperature`` /
                              ``segment_extent``, or its extent reaches ``pipeline_extent``
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from fgen.data.schema import GameData
from fgen.geom import belt as beltgeom
from fgen.geom import fluid, footprint, inserter, mining, pole
from fgen.geom.vec import Direction, Tile
from fgen.layout import belt_run
from fgen.layout.model import Interface, Layout, Placement, Unit

ITEM_HOLDER_TYPES = frozenset(
    {
        "transport-belt",
        "underground-belt",
        "splitter",
        "furnace",
        "assembling-machine",
        "container",
    }
)


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    tiles: frozenset[Tile] = field(default_factory=frozenset)


def _underground_type(p: Placement) -> str:
    # Missing "type" is treated as an entrance (see VERIFY.md).
    return str(p.extra.get("type", "input"))


class _Ctx:
    def __init__(self, layout: Layout, gd: GameData) -> None:
        self.gd = gd
        self.placements = layout.placements
        self.tiles: dict[int, frozenset[Tile]] = {}
        self.violations: list[Violation] = []
        for i, p in enumerate(self.placements):
            if p.name not in gd.entities:
                self.violations.append(
                    Violation("entity.unknown", f"#{i} {p.name}: not in game data")
                )
                continue
            try:
                self.tiles[i] = footprint.tiles_of(gd, p.name, p.position, p.direction)
            except ValueError as exc:
                self.violations.append(
                    Violation(
                        "position.misaligned", f"#{i}: {exc}", frozenset({p.position.floor()})
                    )
                )
        self.at: dict[Tile, list[int]] = {}
        for i, ts in self.tiles.items():
            for t in ts:
                self.at.setdefault(t, []).append(i)

    def type_of(self, i: int) -> str:
        return self.gd.entity(self.placements[i].name).type

    def known(self) -> Iterable[tuple[int, Placement]]:
        for i in self.tiles:
            yield i, self.placements[i]

    def occupants(self, t: Tile) -> list[int]:
        return self.at.get(t, [])


def _check_overlap(c: _Ctx) -> None:
    pairs: dict[tuple[int, int], set[Tile]] = {}
    for t, occ in c.at.items():
        for a in range(len(occ)):
            for b in range(a + 1, len(occ)):
                pairs.setdefault((occ[a], occ[b]), set()).add(t)
    for (a, b), ts in sorted(pairs.items()):
        pa, pb = c.placements[a], c.placements[b]
        c.violations.append(
            Violation("overlap", f"#{a} {pa.name} overlaps #{b} {pb.name}", frozenset(ts))
        )


def _check_inserters(c: _Ctx, item_iface_tiles: frozenset[Tile]) -> None:
    for i, p in c.known():
        if c.type_of(i) != "inserter" or not p.direction.is_cardinal:
            continue
        for code, fn in (
            ("inserter.pickup_empty", inserter.pickup_tile),
            ("inserter.drop_empty", inserter.drop_tile),
        ):
            t = fn(c.gd, p.name, p.position, p.direction)
            if t in item_iface_tiles:
                continue
            if any(c.type_of(j) in ITEM_HOLDER_TYPES for j in c.occupants(t) if j != i):
                continue
            what = "pickup" if code.endswith("pickup_empty") else "drop"
            c.violations.append(
                Violation(code, f"#{i} {p.name}: nothing to {what} at {t}", frozenset({t}))
            )


def _poles(c: _Ctx) -> list[int]:
    return [i for i, _ in c.known() if c.type_of(i) == "electric-pole"]


def _check_power(c: _Ctx) -> None:
    poles = _poles(c)
    supplied: set[Tile] = set()
    for i in poles:
        p = c.placements[i]
        supplied |= pole.supply_tiles(c.gd, p.name, p.position)
    for i, p in c.known():
        e = c.gd.entity(p.name)
        if e.type == "electric-pole" or not e.is_electric:
            continue
        if not (c.tiles[i] & supplied):
            c.violations.append(
                Violation(
                    "power.uncovered", f"#{i} {p.name}: outside every supply area", c.tiles[i]
                )
            )
    # connectivity
    seen: set[int] = set()
    components: list[list[int]] = []
    for start in poles:
        if start in seen:
            continue
        comp, stack = [], [start]
        seen.add(start)
        while stack:
            a = stack.pop()
            comp.append(a)
            pa = c.placements[a]
            for b in poles:
                if b in seen:
                    continue
                pb = c.placements[b]
                if pole.wire_reaches(c.gd, pa.name, pa.position, pb.name, pb.position):
                    seen.add(b)
                    stack.append(b)
        components.append(comp)
    if len(components) > 1:
        components.sort(key=len, reverse=True)
        for comp in components[1:]:
            ts = frozenset().union(*(c.tiles[i] for i in comp))
            c.violations.append(
                Violation(
                    "power.disconnected",
                    f"{len(components)} pole networks; poles {sorted(comp)} not wired to the main",
                    ts,
                )
            )


def _belt_accepts(c: _Ctx, t: Tile, flow: Direction) -> bool:
    for j in c.occupants(t):
        pj = c.placements[j]
        tj = c.type_of(j)
        if tj == "transport-belt" and pj.direction != flow.opposite:
            return True
        if tj == "underground-belt" and _underground_type(pj) == "input" and pj.direction == flow:
            return True
        if tj == "splitter" and pj.direction == flow:
            return True
    return False


def _check_belts(c: _Ctx, out_tiles: frozenset[Tile], terminals: frozenset[Tile]) -> None:
    for i, p in c.known():
        tp = c.type_of(i)
        if not p.direction.is_cardinal:
            continue
        if tp == "transport-belt" or (
            tp == "underground-belt" and _underground_type(p) == "output"
        ):
            (t,) = c.tiles[i]
            if t in out_tiles or t in terminals:
                continue
            nxt = t.neighbor(p.direction)
            if not _belt_accepts(c, nxt, p.direction):
                c.violations.append(
                    Violation(
                        "belt.discontinuous",
                        f"#{i} {p.name} at {t} flows {p.direction.name} into nothing",
                        frozenset({t, nxt}),
                    )
                )


def _check_undergrounds(c: _Ctx) -> None:
    for i, p in c.known():
        if c.type_of(i) != "underground-belt" or not p.direction.is_cardinal:
            continue
        reach = int(c.gd.entity(p.name).max_distance or 0)
        kind = _underground_type(p)
        step = p.direction if kind == "input" else p.direction.opposite
        want = "output" if kind == "input" else "input"
        (t,) = c.tiles[i]
        paired = False
        for k in range(1, reach + 1):
            hit = [
                j
                for j in c.occupants(t.neighbor(step, k))
                if c.placements[j].name == p.name and c.placements[j].direction == p.direction
            ]
            if hit:
                paired = _underground_type(c.placements[hit[0]]) == want
                break
        if not paired:
            c.violations.append(
                Violation(
                    "underground.unpaired",
                    f"#{i} {p.name} ({kind}) at {t}: no {want} within {reach} tiles",
                    frozenset({t}),
                )
            )


def _check_fluids(c: _Ctx, fluids: Mapping[int, str], fluid_tiles: frozenset[Tile]) -> None:
    """Touching connections with different fluids, or an idle machine port touching a
    fluid entity (``fluid.unintended_adjacency``); a machine's recipe fluid box with no
    connection of its fluid (``fluid.box_unconnected``)."""
    ports: dict[tuple[Tile, Direction], tuple[int, str | None]] = {}
    for i, p in c.known():
        if not fluid.has_fluid_ports(c.gd, p.name) or not p.direction.is_cardinal:
            continue
        for t, d, lab in fluid.entity_ports(
            c.gd, p.name, p.position, p.direction, p.extra, fluids.get(i)
        ):
            ports[(t, d)] = (i, lab)
    reported: set[tuple[int, int]] = set()
    for (t, d), (a, fa) in ports.items():
        n = t.neighbor(d)
        hit = ports.get((n, d.opposite))
        if hit is None or hit[0] == a:
            continue
        b, fb = hit
        key = (min(a, b), max(a, b))
        if key in reported:
            continue
        idle = (fa == fluid.IDLE) != (fb == fluid.IDLE)
        if idle or (fa is not None and fb is not None and fa != fb):
            reported.add(key)
            what = "an idle port" if idle else f"{fb}"
            c.violations.append(
                Violation(
                    "fluid.unintended_adjacency",
                    f"#{a} ({fa}) connects to #{b} ({what})",
                    frozenset({t, n}),
                )
            )
    # every fluid box a machine's recipe uses has some port facing its fluid (an
    # unknown fluid or a fluid interface tile counts as connected)
    for i, p in c.known():
        if not c.gd.entity(p.name).fluid_boxes or not p.direction.is_cardinal:
            continue
        rec = p.extra.get("recipe")
        pts = fluid.machine_ports(
            c.gd,
            p.name,
            p.position,
            p.direction,
            str(rec) if rec else None,
            fluid.mirrored(p.extra),
        )
        # one box per (role, fluid): a recipe's single fluid merges the boxes of its kind
        boxes: dict[tuple[str, str], list[fluid.Port]] = {}
        for pt in pts:
            if pt.fluid is not None:
                boxes.setdefault((pt.role, pt.fluid), []).append(pt)
        for box, bps in sorted(boxes.items()):
            ok = False
            for pt in bps:
                hit = ports.get((pt.outside, pt.direction.opposite))
                if pt.outside in fluid_tiles or (
                    hit is not None and hit[0] != i and hit[1] in (pt.fluid, None)
                ):
                    ok = True
                    break
            if not ok:
                c.violations.append(
                    Violation(
                        "fluid.box_unconnected",
                        f"#{i} {p.name}: {box[0]} box ({box[1]}) faces no connection of its fluid",
                        frozenset(pt.outside for pt in bps),
                    )
                )


def _check_pipe_undergrounds(c: _Ctx) -> None:
    """Every pipe-to-ground pairs with the nearest one on its underground side (an
    opposite-facing one of the same name within ``max_underground_distance``; a
    same-facing one in between takes the pair, VERIFY §13)."""
    for i, p in c.known():
        e = c.gd.entity(p.name)
        if e.type != "pipe-to-ground" or not p.direction.is_cardinal:
            continue
        reach = int(e.max_underground_distance or 0)
        step = p.direction.opposite  # the underground side
        (t,) = c.tiles[i]
        paired = False
        for k in range(1, reach + 1):
            hit = [
                j
                for j in c.occupants(t.neighbor(step, k))
                if c.placements[j].name == p.name
                and c.placements[j].direction in (p.direction, p.direction.opposite)
            ]
            if hit:
                paired = c.placements[hit[0]].direction == p.direction.opposite
                break
        if not paired:
            c.violations.append(
                Violation(
                    "fluid.underground_unpaired",
                    f"#{i} {p.name} at {t}: no partner within {reach} tiles",
                    frozenset({t}),
                )
            )


DRILL_OUTPUT_TYPES = frozenset({"transport-belt", "underground-belt", "splitter", "container"})


def _check_drills(
    c: _Ctx,
    item_iface_tiles: frozenset[Tile],
    resources: Mapping[Tile, tuple[str, int]] | None,
) -> None:
    for i, p in c.known():
        e = c.gd.entity(p.name)
        if e.type != "mining-drill" or not p.direction.is_cardinal:
            continue
        if e.vector_to_place_result is not None and e.output_fluid_box is None:
            t = mining.drop_tile(c.gd, p.name, p.position, p.direction)
            if t not in item_iface_tiles and not any(
                c.type_of(j) in DRILL_OUTPUT_TYPES for j in c.occupants(t) if j != i
            ):
                c.violations.append(
                    Violation(
                        "drill.output_blocked",
                        f"#{i} {p.name}: output tile {t} holds no belt or container",
                        frozenset({t}),
                    )
                )
        if resources is None or e.resource_searching_radius is None:
            continue
        ok = False
        for t in mining.search_tiles(c.gd, p.name, p.position):
            r = resources.get(t)
            if r is None:
                continue
            name = r[0]
            if name not in c.gd.resources or mining.can_mine(c.gd, p.name, name):
                ok = True
                break
        if not ok:
            c.violations.append(
                Violation(
                    "drill.no_ore", f"#{i} {p.name}: nothing to mine in its search area", c.tiles[i]
                )
            )


def validate(
    layout: Layout,
    gd: GameData,
    interfaces: Sequence[Interface] = (),
    *,
    belt_terminals: Iterable[Tile] = (),
    fluids: Mapping[int, str] | None = None,
    resources: Mapping[Tile, tuple[str, int]] | None = None,
) -> list[Violation]:
    """Check every invariant. ``belt_terminals`` are tiles where a belt is meant to end
    (e.g. the far end of a feed belt); ``fluids`` maps placement index -> fluid name;
    ``resources`` (tile -> (name, amount), e.g. ``Terrain.resources``) enables
    ``drill.no_ore``."""
    c = _Ctx(layout, gd)
    item_ifaces = frozenset(i.tile for i in interfaces if i.kind == "item")
    out_ifaces = frozenset(i.tile for i in interfaces if i.kind == "item" and i.role == "output")
    _check_overlap(c)
    _check_inserters(c, item_ifaces)
    _check_power(c)
    _check_belts(c, out_ifaces, frozenset(belt_terminals))
    _check_undergrounds(c)
    fluid_ifaces = frozenset(i.tile for i in interfaces if i.kind == "fluid")
    _check_fluids(c, fluids or {}, fluid_ifaces)
    _check_pipe_undergrounds(c)
    _check_drills(c, item_ifaces, resources)
    return c.violations


def validate_unit(
    unit: Unit, gd: GameData, resources: Mapping[Tile, tuple[str, int]] | None = None
) -> list[Violation]:
    """``validate`` with the unit's interfaces and ``meta['belt_terminals']``, plus the
    contract checks (``contract.*``)."""
    out = validate(
        unit.layout,
        gd,
        unit.interfaces,
        belt_terminals=unit.meta.get("belt_terminals", ()),
        fluids=unit.meta.get("fluids"),
        resources=resources,
    )
    return out + check_contract(unit, gd)


_PORT_ENTITY_TYPES = {
    "item": frozenset({"transport-belt", "underground-belt", "splitter", "loader", "loader-1x1"}),
    "power": frozenset({"electric-pole"}),
}


def check_contract(unit: Unit, gd: GameData) -> list[Violation]:
    """The interface fields the router reads agree with the unit (``contract.*``)."""
    out: list[Violation] = []
    at: dict[Tile, list[str]] = {}
    for p in unit.layout.placements:
        if p.name in gd.entities:
            for t in p.tiles(gd):
                at.setdefault(t, []).append(p.name)
    groups = {g.name for g in unit.groups}
    first: dict[str, tuple[Tile, Direction]] = {}
    fluid_conns: dict[tuple[Tile, Direction], set[str]] = {}
    for p in unit.layout.placements:
        if p.name in gd.entities and fluid.has_fluid_ports(gd, p.name):
            for t, d, _ in fluid.entity_ports(gd, p.name, p.position, p.direction, p.extra):
                fluid_conns.setdefault((t, d), set()).add(p.name)
    for k, i in enumerate(unit.interfaces):
        where = frozenset({i.tile})
        if i.kind == "fluid":
            bad = ""
            if i.entity is None or i.entity not in fluid_conns.get((i.tile, i.direction), set()):
                bad = (
                    f"names {i.entity!r}, no such connection at {i.tile} facing {i.direction.name}"
                )
            elif i.temperature is None or i.segment_extent is None:
                bad = "temperature and segment_extent are required"
            elif i.segment_extent >= gd.constant("pipeline_extent"):
                bad = f"segment extent {i.segment_extent} reaches the pipeline extent"
            if bad:
                out.append(Violation("contract.fluid", f"interface {k}: {bad}", where))
        types = _PORT_ENTITY_TYPES.get(i.kind)
        if types is not None:
            here = [n for n in at.get(i.tile, []) if gd.entity(n).type in types]
            if i.entity is None or i.entity not in here:
                out.append(
                    Violation(
                        "contract.entity",
                        f"interface {k} ({i.kind} at {i.tile}) names {i.entity!r}, "
                        f"the tile holds {here or 'no such entity'}",
                        where,
                    )
                )
        if i.kind == "item":
            if i.space is None:
                out.append(
                    Violation("contract.space", f"interface {k}: item port without space", where)
                )
            elif i.space.approach == "side" and i.lane not in ("left", "right"):
                out.append(
                    Violation(
                        "contract.space", f"interface {k}: side approach on lane {i.lane}", where
                    )
                )
            if i.buffer_items is None:
                out.append(
                    Violation(
                        "contract.buffer", f"interface {k}: item port without buffer_items", where
                    )
                )
            elif here:
                n = belt_run.run_tiles(unit.layout, gd, i.tile, i.role)
                want = beltgeom.run_items(gd, n, i.lane)
                if abs(i.buffer_items - want) > 1e-6:
                    out.append(
                        Violation(
                            "contract.buffer",
                            f"interface {k}: buffer_items {i.buffer_items:g}, the {n}-tile "
                            f"belt run on lane {i.lane} holds {want:g}",
                            where,
                        )
                    )
        rp = i.rate_profile
        if rp is not None:
            bad = ""
            if rp.supply == "fixed" and (
                rp.initial is None or i.rate is None or abs(rp.initial - i.rate) > 1e-9
            ):
                bad = f"rate {i.rate} vs profile initial {rp.initial}"
            elif rp.supply == "on_demand" and i.rate is not None:
                bad = f"on-demand port with a fixed rate {i.rate}"
            elif rp.lanes:
                used = {"left", "right"} if i.lane in (None, "both") else {i.lane}
                if not {ln for ln, _ in rp.lanes} <= used:
                    bad = f"per-lane rates {dict(rp.lanes)} on a {i.lane} port"
                elif (
                    rp.supply == "fixed"
                    and abs(sum(v for _, v in rp.lanes) - (rp.initial or 0)) > 1e-9
                ):
                    bad = f"per-lane rates {dict(rp.lanes)} do not add up to {rp.initial}"
            if bad:
                out.append(Violation("contract.rate", f"interface {k}: {bad}", where))
        if i.port_group is not None:
            if i.port_group not in groups:
                out.append(
                    Violation("contract.group", f"interface {k}: no group {i.port_group!r}", where)
                )
            elif first.setdefault(i.port_group, (i.tile, i.direction)) != (i.tile, i.direction):
                out.append(
                    Violation(
                        "contract.group",
                        f"interface {k}: group {i.port_group!r} spans more than one port tile",
                        where,
                    )
                )
    return out
