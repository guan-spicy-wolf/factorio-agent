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
``fluid.unintended_adjacency`` two touching fluid connections carry different fluids
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from fgen.data.schema import GameData
from fgen.geom import fluid, footprint, inserter, pole
from fgen.geom.vec import Direction, Tile
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


def _check_fluids(c: _Ctx, fluids: Mapping[int, str]) -> None:
    ports: dict[tuple[Tile, Direction], int] = {}
    for i, p in c.known():
        e = c.gd.entity(p.name)
        if (e.fluid_box is None and e.output_fluid_box is None) or not p.direction.is_cardinal:
            continue
        for t, d in fluid.connection_points(c.gd, p.name, p.position, p.direction):
            ports[(t, d)] = i
    reported: set[tuple[int, int]] = set()
    for (t, d), a in ports.items():
        n = t.neighbor(d)
        b = ports.get((n, d.opposite))
        if b is None or b == a:
            continue
        key = (min(a, b), max(a, b))
        fa, fb = fluids.get(a), fluids.get(b)
        if fa is not None and fb is not None and fa != fb and key not in reported:
            reported.add(key)
            c.violations.append(
                Violation(
                    "fluid.unintended_adjacency",
                    f"#{a} ({fa}) connects to #{b} ({fb})",
                    frozenset({t, n}),
                )
            )


def validate(
    layout: Layout,
    gd: GameData,
    interfaces: Sequence[Interface] = (),
    *,
    belt_terminals: Iterable[Tile] = (),
    fluids: Mapping[int, str] | None = None,
) -> list[Violation]:
    """Check every invariant. ``belt_terminals`` are tiles where a belt is meant to end
    (e.g. the far end of a feed belt); ``fluids`` maps placement index -> fluid name."""
    c = _Ctx(layout, gd)
    item_ifaces = frozenset(i.tile for i in interfaces if i.kind == "item")
    out_ifaces = frozenset(i.tile for i in interfaces if i.kind == "item" and i.role == "output")
    _check_overlap(c)
    _check_inserters(c, item_ifaces)
    _check_power(c)
    _check_belts(c, out_ifaces, frozenset(belt_terminals))
    _check_undergrounds(c)
    _check_fluids(c, fluids or {})
    return c.violations


def validate_unit(unit: Unit, gd: GameData) -> list[Violation]:
    """``validate`` with the unit's interfaces and ``meta['belt_terminals']``."""
    return validate(
        unit.layout,
        gd,
        unit.interfaces,
        belt_terminals=unit.meta.get("belt_terminals", ()),
        fluids=unit.meta.get("fluids"),
    )
