"""Periods of a direct-insertion chain and their boundary conditions (plan-phase4a §3).

A **period** is a ``width x height`` rectangle of placements (period coordinates: tile
(0, 0) is its top-left tile, x east, y south) that is repeated ``n`` times along x with
step ``width``. Belts run east through the whole period on fixed rows, so the copies
join into continuous belts. The **boundary conditions** a period must meet so that any
number of copies is valid (and each copy works on its own, which keeps the two ends of
a chain fed and powered):

1. every footprint lies inside the rectangle (nothing straddles the cut);
2. every belt row holds one east-facing belt of the cycle's tier on each of its
   ``width`` tiles (so the first tile of copy k+1 continues the last tile of copy k);
   with a palette (plan-phase4c) the row's tier is the row's own, and an underground
   pair of that tier's underground belt may replace a run of the row's tiles, wholly
   inside the period (entrance, free tiles, exit; first and last tile stay plain);
3. every inserter's pickup and drop tiles lie inside the rectangle: items move only
   within a period, never across the cut;
4. every electric consumer is supplied by a pole of its **own** period;
5. the period's poles form one wire network, and some pole reaches the image of some
   pole in the next period (``x + width``): the copies' networks join into one.

Only (5) couples neighbouring periods. :func:`check_period` checks these directly;
the final judge is :func:`fgen.layout.validate.validate` on the tiled layout.

The interface of a period is its belt rows (:class:`BeltRow`): which material enters
on which lane, which product leaves on which lane, at what rate per period. The
intermediate (e.g. copper cable) never touches a belt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Literal

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.geom import footprint, inserter, pole
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout.model import Layout, Placement

FLOW = Direction.E  # belts flow east; periods repeat along +x
_EPS = 1e-9

type Lane = Literal["left", "right"]


@dataclass(frozen=True)
class Stage:
    """``count`` identical machines crafting ``recipe`` in one period."""

    recipe: str
    machine: str
    count: int


@dataclass(frozen=True)
class CycleSpec:
    """What the solver places: the stages and the entity tiers (plan-phase4a §1.1)."""

    stages: tuple[Stage, ...]
    inserter: str = "fast-inserter"
    pole: str = "small-electric-pole"
    belt: str = "transport-belt"
    extra_inserters: tuple[str, ...] = ()  # further inserter tiers the solver may mix in
    # intermediates that travel on a lane of an input belt inside the period instead of
    # machine to machine (dropped upstream of every pickup, surplus backs the lane up)
    lane_items: tuple[str, ...] = ()
    # fluids (plan-phase5a): pipe entities the solver may place, machines' mirror flag
    pipe: str = "pipe"
    pipe_to_ground: str | None = "pipe-to-ground"
    mirror: bool = False
    # palette (plan-phase4c): further pole and belt tiers the solver may choose from (per
    # pole, per belt row), and underground belts it may put into a belt row (each row's
    # tier with its own underground, matched by speed); every one may go unused
    extra_poles: tuple[str, ...] = ()
    extra_belts: tuple[str, ...] = ()
    undergrounds: tuple[str, ...] = ()

    @property
    def inserters(self) -> tuple[str, ...]:
        return (self.inserter, *(i for i in self.extra_inserters if i != self.inserter))

    @property
    def poles(self) -> tuple[str, ...]:
        return (self.pole, *(i for i in self.extra_poles if i != self.pole))

    @property
    def belts(self) -> tuple[str, ...]:
        return (self.belt, *(i for i in self.extra_belts if i != self.belt))

    def single_tier(self) -> bool:
        """One pole tier, one belt tier, no undergrounds (the phase-4a vocabulary)."""
        return len(self.poles) == 1 and len(self.belts) == 1 and not self.undergrounds

    def machines(self) -> int:
        return sum(s.count for s in self.stages)


@dataclass(frozen=True)
class StageFlow:
    """Per-machine numbers of a stage at full speed (items/s)."""

    product: str
    rate: float  # main product per machine
    internal_in: dict[str, float]  # ingredient made by another stage -> rate
    external_in: dict[str, float]  # ingredient from a belt -> rate
    final: bool  # product leaves the period on a belt


@dataclass(frozen=True)
class CycleFlows:
    stages: tuple[StageFlow, ...]
    inputs: tuple[str, ...]  # external materials, sorted
    outputs: tuple[str, ...]  # products leaving on belts, sorted
    output_rate: dict[str, float]  # per period
    input_rate: dict[str, float]  # per period, at the output rate (not machine capacity)
    internal: dict[str, tuple[float, float]]  # item -> (supply, demand) per period
    surplus: dict[str, float]  # internal item -> supply / demand - 1 (0 = exact ratio)
    share: tuple[float, ...] = ()  # per stage: fraction of full speed it runs at
    fluid_inputs: tuple[str, ...] = ()  # external fluids (pipes), sorted
    fluid_outputs: tuple[str, ...] = ()  # fluid products leaving on pipes, sorted


def flows(spec: CycleSpec, gd: GameData) -> CycleFlows:
    """Derive every rate from the recipes and machines (``gen.throughput``)."""
    products = [tp.main_product(gd, s.recipe)[0] for s in spec.stages]
    made = set(products)
    out: list[StageFlow] = []
    for s, prod in zip(spec.stages, products, strict=True):
        ing = tp.ingredient_rates(gd, s.recipe, s.machine)
        consumed = any(
            prod in tp.ingredient_rates(gd, o.recipe, o.machine) for o in spec.stages if o is not s
        )
        out.append(
            StageFlow(
                product=prod,
                rate=tp.machine_rate(gd, s.recipe, s.machine),
                internal_in={k: v for k, v in ing.items() if k in made},
                external_in={k: v for k, v in ing.items() if k not in made},
                final=not consumed,
            )
        )
    # shares (fraction of full speed each stage runs at), from the final stages down:
    # a producer runs only as fast as its consumers (at their own shares) use its product
    share = [1.0 if sf.final else None for sf in out]
    internal: dict[str, tuple[float, float]] = {}
    pending = set(made)
    while pending:
        ready = [
            item
            for item in sorted(pending)
            if all(share[k] is not None for k, sf in enumerate(out) if item in sf.internal_in)
        ]
        if not ready:
            raise ValueError(f"cyclic intermediates: {sorted(pending)}")
        for item in ready:
            pending.discard(item)
            demand = sum(
                sf.internal_in.get(item, 0.0) * s.count * float(share[k] or 0.0)
                for k, (s, sf) in enumerate(zip(spec.stages, out, strict=True))
            )
            supply = sum(
                sf.rate * s.count
                for s, sf in zip(spec.stages, out, strict=True)
                if sf.product == item
            )
            if demand:
                if supply < demand - _EPS:
                    raise ValueError(f"{item}: period makes {supply:g}/s but uses {demand:g}/s")
                internal[item] = (supply, demand)
            for k, sf in enumerate(out):
                if sf.product == item and not sf.final:
                    share[k] = demand / supply if supply else 0.0
    input_rate: dict[str, float] = {}
    output_rate: dict[str, float] = {}
    for s, sf, sh in zip(spec.stages, out, share, strict=True):
        if sf.final:
            output_rate[sf.product] = output_rate.get(sf.product, 0.0) + sf.rate * s.count
        for k, v in sf.external_in.items():
            input_rate[k] = input_rate.get(k, 0.0) + v * s.count * float(sh or 0.0)
    externals = {k for sf in out for k in sf.external_in}
    return CycleFlows(
        stages=tuple(out),
        inputs=tuple(sorted(k for k in externals if not gd.is_fluid(k))),
        outputs=tuple(sorted(k for k in output_rate if not gd.is_fluid(k))),
        output_rate=output_rate,
        input_rate=input_rate,
        internal=internal,
        surplus={k: s / d - 1 for k, (s, d) in internal.items()},
        share=tuple(float(x or 0.0) for x in share),
        fluid_inputs=tuple(sorted(k for k in externals if gd.is_fluid(k))),
        fluid_outputs=tuple(sorted(k for k in output_rate if gd.is_fluid(k))),
    )


def ratio(gd: GameData, consumer: tuple[str, str], producer: tuple[str, str]) -> Fraction:
    """Exact producer : consumer machine ratio (as producers per consumer) for one
    intermediate, e.g. 3/2 for copper cable -> electronic circuit."""
    item = tp.main_product(gd, producer[0])[0]
    need = tp.ingredient_rates(gd, *consumer)[item]
    make = tp.machine_rate(gd, *producer)
    return Fraction(need / make).limit_denominator(1000)


@dataclass(frozen=True)
class BeltRow:
    """One belt row of a period: ``lanes`` maps lane -> material and ``rates`` lane ->
    items/s per period (consumed for inputs, produced for outputs)."""

    y: int
    role: Literal["input", "output"]
    lanes: tuple[tuple[Lane, str], ...]
    rates: tuple[tuple[Lane, float], ...] = ()
    belt: str = ""  # the row's belt tier ("" = the spec's ``belt``)

    def materials(self) -> frozenset[str]:
        return frozenset(m for _, m in self.lanes)


@dataclass(frozen=True)
class FluidCrossing:
    """Where a fluid's pipes cross the period cut: plain pipes on ``(0, y)`` and
    ``(width - 1, y)`` (the copies' pipes join there; the unit's port). ``rate``: fluid
    units/s per period, consumed (input) or produced (output)."""

    y: int
    role: Literal["input", "output"]
    fluid: str
    rate: float


@dataclass(frozen=True)
class Period:
    width: int
    height: int
    placements: tuple[Placement, ...]
    belts: tuple[BeltRow, ...]
    spec: CycleSpec
    stage_of: tuple[int | None, ...]  # per placement: stage index for machines
    meta: dict[str, Any] = field(default_factory=dict)
    pipes: tuple[FluidCrossing, ...] = ()
    fluid_of: tuple[str | None, ...] = ()  # per placement: fluid of pipes (validate)

    @property
    def area(self) -> int:
        return self.width * self.height

    def layout(self) -> Layout:
        return Layout(list(self.placements))


def tile(period: Period, n: int) -> Layout:
    """``n`` copies of the period, copy k shifted by ``k * width`` tiles east."""
    out: list[Placement] = []
    for k in range(n):
        off = Vec2(k * period.width, 0)
        out += [
            Placement(p.name, p.position + off, p.direction, dict(p.extra))
            for p in period.placements
        ]
    return Layout(out)


def check_period(period: Period, gd: GameData) -> list[str]:
    """The boundary conditions of the module docstring; returns problems (empty = ok)."""
    w, h = period.width, period.height
    problems: list[str] = []
    inside = {Tile(x, y) for x in range(w) for y in range(h)}
    at: dict[Tile, int] = {}
    for i, p in enumerate(period.placements):
        ts = p.tiles(gd)
        if not ts <= inside:
            problems.append(f"#{i} {p.name} leaves the {w}x{h} period")
        for t in ts:
            at[t] = i
    for b in period.belts:
        problems += _check_row(period, gd, b, at)
    for c in period.pipes:
        for x in (0, w - 1):
            i = at.get(Tile(x, c.y))
            p = period.placements[i] if i is not None else None
            if p is None or p.name != period.spec.pipe or period.fluid_of[i] != c.fluid:
                problems.append(f"{c.fluid} crossing y={c.y}: tile x={x} is not its pipe")
    for i, p in enumerate(period.placements):
        if gd.entity(p.name).type != "inserter":
            continue
        for what, fn in (("pickup", inserter.pickup_tile), ("drop", inserter.drop_tile)):
            t = fn(gd, p.name, p.position, p.direction)
            if t not in inside:
                problems.append(f"#{i} {p.name}: {what} tile {t} is outside the period")
    poles = [p for p in period.placements if gd.entity(p.name).type == "electric-pole"]
    supplied: set[Tile] = set()
    for q in poles:
        supplied |= pole.supply_tiles(gd, q.name, q.position)
    for i, p in enumerate(period.placements):
        e = gd.entity(p.name)
        if e.type != "electric-pole" and e.is_electric and not (p.tiles(gd) & supplied):
            problems.append(f"#{i} {p.name}: no pole of its own period supplies it")
    if poles:
        seen, stack = {0}, [0]
        while stack:
            a = stack.pop()
            for b in range(len(poles)):
                if b not in seen and pole.wire_reaches(
                    gd, poles[a].name, poles[a].position, poles[b].name, poles[b].position
                ):
                    seen.add(b)
                    stack.append(b)
        if len(seen) != len(poles):
            problems.append("the period's poles are not one wire network")
        shift = Vec2(w, 0)
        if not any(
            pole.wire_reaches(gd, a.name, a.position, b.name, b.position + shift)
            for a in poles
            for b in poles
        ):
            problems.append("no pole reaches a pole of the next period")
    return problems


def _check_row(period: Period, gd: GameData, b: BeltRow, at: dict[Tile, int]) -> list[str]:
    """Condition 2 with undergrounds: the row is east-facing belts of its tier, except
    underground pairs of that tier's underground (entrance, free tiles, exit) wholly
    inside the period; the first and the last tile are plain belts."""
    belt = b.belt or period.spec.belt
    ug = underground_of(gd, belt, period.spec.undergrounds)
    out: list[str] = []
    x, w = 0, period.width
    while x < w:
        i = at.get(Tile(x, b.y))
        p = period.placements[i] if i is not None else None
        if p is not None and p.name == belt and p.direction == FLOW:
            x += 1
            continue
        if (
            0 < x < w - 1
            and p is not None
            and p.name == ug
            and p.direction == FLOW
            and p.extra.get("type") == "input"
        ):
            reach, k, q = int(gd.entity(ug).max_distance or 0), 0, None
            for k in range(1, reach + 1):
                j = at.get(Tile(x + k, b.y))
                q = period.placements[j] if j is not None else None
                if q is not None and q.name == ug:
                    break
            if (
                x + k < w - 1
                and q is not None
                and q.name == ug
                and q.direction == FLOW
                and q.extra.get("type") == "output"
            ):
                x += k + 1
                continue
        out.append(f"belt row y={b.y}: tile x={x} is not a {FLOW.name} {belt} or underground pair")
        x += 1
    return out


def underground_of(gd: GameData, belt: str, allowed: tuple[str, ...]) -> str | None:
    """The underground belt of ``belt``'s tier (same speed) among ``allowed``."""
    speed = gd.entity(belt).speed
    for n in allowed:
        if gd.entity(n).speed == speed:
            return n
    return None


def ceil_div(a: float, b: float) -> int:
    return max(0, math.ceil(a / b - _EPS))


def center(gd: GameData, name: str, x: int, y: int, d: Direction = Direction.N) -> Vec2:
    return footprint.center_for_tile_anchor(gd, name, Tile(x, y), d)


# ---- shared by the solver backends -------------------------------------------------


@dataclass(frozen=True)
class SkeletonBelt:
    """A belt of a skeleton: ``role`` input (``materials``: one per lane, or one for
    both lanes) or output (``materials``: the product)."""

    role: Literal["input", "output"]
    materials: tuple[str, ...]


def belt_sets(
    f: CycleFlows, lanes: int, lane_items: tuple[str, ...] = ()
) -> list[list[SkeletonBelt]]:
    """External inputs (and lane intermediates) partitioned into belts of at most
    ``lanes`` materials, plus one output belt per product (every skeleton the backends
    consider)."""

    def partitions(items: list[str]) -> list[list[list[str]]]:
        if not items:
            return [[]]
        first, rest = items[0], items[1:]
        out: list[list[list[str]]] = []
        for part in partitions(rest):
            for k in range(len(part)):
                if len(part[k]) < lanes:
                    out.append([*part[:k], [first, *part[k]], *part[k + 1 :]])
            out.append([[first], *part])
        return out

    outs = [SkeletonBelt("output", (o,)) for o in f.outputs]
    return [
        [SkeletonBelt("input", tuple(sorted(b))) for b in part] + outs
        for part in partitions(sorted({*f.inputs, *lane_items}))
    ]


def belt_rate(f: CycleFlows, material: str) -> float:
    """Items/s per period a material puts on an input belt: a raw input's consumption,
    a lane intermediate's use."""
    if material in f.input_rate:
        return f.input_rate[material]
    return f.internal[material][1]


def input_lanes_ok(
    belts: list[SkeletonBelt], f: CycleFlows, lane_cap: float, lanes: int, repeats: int
) -> bool:
    """Every input lane carries ``repeats`` periods' worth of its material."""
    for b in belts:
        if b.role != "input":
            continue
        n = 1 if len(b.materials) > 1 else lanes
        for m in b.materials:
            if belt_rate(f, m) * repeats > lane_cap * n + _EPS:
                return False
    return True


@dataclass(frozen=True)
class PlacedInserter:
    """An inserter of a solved period (tile ``x, y``). ``kind``: ``in`` (belt
    ``other`` -> machine ``machine``), ``out`` (machine ``machine`` -> belt ``other``,
    landing on ``lane``) or ``link`` (machine ``other`` -> machine ``machine``)."""

    x: int
    y: int
    direction: Direction
    tier: str
    kind: str
    machine: int
    other: int
    lane: str | None = None


def assemble(
    spec: CycleSpec,
    gd: GameData,
    width: int,
    height: int,
    belts: list[SkeletonBelt],
    rows: list[int],
    machines: list[tuple[int, int, int]],
    inserters: list[PlacedInserter],
    poles: list[tuple[int, int]],
    f: CycleFlows,
    lane_sides: dict[int, dict[str, str]] | None = None,
    directions: dict[int, Direction] | None = None,
    pipes: list[tuple[int, int, str, Direction | None]] | None = None,
    crossings: dict[str, int] | None = None,
    belt_tiers: list[str] | None = None,
    belt_undergrounds: dict[int, dict[int, str]] | None = None,
) -> Period:
    """The :class:`Period` of a solution: ``belts[k]`` on row ``rows[k]``, machines as
    ``(stage, x, y)`` anchors (top-left tile), poles as anchors. Output-belt lane rates
    split each machine's product evenly over its output inserters. Fluids: machine
    ``directions`` (by index in ``machines``; default N), ``pipes`` as ``(x, y, fluid,
    None)`` for a pipe or ``(..., d)`` for a pipe-to-ground whose normal connection faces
    ``d``, ``crossings`` fluid -> row of its crossing. Palette: ``belt_tiers[k]`` is belt
    ``k``'s tier (default the spec's), ``belt_undergrounds[k]`` maps x -> ``input`` /
    ``output`` / ``gap`` for the underground pairs in its row; poles may be ``(name, x,
    y)``."""
    ps: list[Placement] = []
    stage_of: list[int | None] = []
    directions = directions or {}
    tiers = belt_tiers or [spec.belt] * len(belts)
    ugs = belt_undergrounds or {}
    for k, y in enumerate(rows):
        ug = underground_of(gd, tiers[k], spec.undergrounds)
        for x in range(width):
            kind = ugs.get(k, {}).get(x)
            if kind == "gap":
                continue
            if kind is not None:
                assert ug is not None
                ps.append(Placement(ug, center(gd, ug, x, y, FLOW), FLOW, {"type": kind}))
            else:
                ps.append(Placement(tiers[k], center(gd, tiers[k], x, y, FLOW), FLOW))
            stage_of.append(None)
    for mi, (si, x, y) in enumerate(machines):
        st = spec.stages[si]
        d = directions.get(mi, Direction.N)
        extra: dict[str, Any] = {"recipe": st.recipe}
        if spec.mirror and gd.entity(st.machine).use_mirroring:
            extra["mirror"] = True
        ps.append(Placement(st.machine, center(gd, st.machine, x, y, d), d, extra))
        stage_of.append(si)
    for s in sorted(inserters, key=lambda s: (s.y, s.x)):
        ps.append(Placement(s.tier, center(gd, s.tier, s.x, s.y), s.direction))
        stage_of.append(None)
    named = [(spec.pole, *q) if len(q) == 2 else q for q in poles]
    for name, x, y in sorted(named, key=lambda q: (q[1], q[2], q[0])):
        ps.append(Placement(name, center(gd, name, x, y)))  # type: ignore[arg-type]
        stage_of.append(None)
    fluid_of: list[str | None] = [None] * len(ps)
    for x, y, fl, d in sorted(pipes or [], key=lambda q: (q[1], q[0], q[2])):
        if d is None:
            ps.append(Placement(spec.pipe, center(gd, spec.pipe, x, y)))
        else:
            assert spec.pipe_to_ground is not None
            ps.append(Placement(spec.pipe_to_ground, center(gd, spec.pipe_to_ground, x, y, d), d))
        stage_of.append(None)
        fluid_of.append(fl)
    crossing_rows = []
    for fl, y in sorted((crossings or {}).items()):
        role: Literal["input", "output"] = "input" if fl in f.fluid_inputs else "output"
        rate = f.input_rate[fl] if role == "input" else f.output_rate[fl]
        crossing_rows.append(FluidCrossing(y, role, fl, rate))
    out_rows: list[BeltRow] = []
    lane_sides = lane_sides or {}
    for k, (b, y) in enumerate(zip(belts, rows, strict=True)):
        if b.role == "input" and k in lane_sides:
            # a lane intermediate on its lane, the other material (if any) on the other
            placed = lane_sides[k]  # lane item -> its lane; raw materials take the rest
            rest = [ln for ln in ("left", "right") if ln not in placed.values()]
            raw = [m for m in b.materials if m not in placed]
            lanes = tuple(
                sorted([*((ln, it) for it, ln in placed.items()), *zip(rest, raw, strict=False)])
            )
            rates = tuple((ln, belt_rate(f, mat)) for ln, mat in lanes)
            out_rows.append(BeltRow(y, "input", lanes, rates, tiers[k]))
        elif b.role == "input":
            if len(b.materials) == 1:
                lanes: tuple[tuple[Any, str], ...] = (
                    ("left", b.materials[0]),
                    ("right", b.materials[0]),
                )
                r = f.input_rate[b.materials[0]] / 2
                rates: tuple[tuple[Any, float], ...] = (("left", r), ("right", r))
            else:
                lanes = tuple(zip(("left", "right"), b.materials, strict=False))
                rates = tuple((ln, f.input_rate[mat]) for ln, mat in lanes)
            out_rows.append(BeltRow(y, "input", lanes, rates, tiers[k]))
        else:
            load: dict[str, float] = {}
            for m in {s.machine for s in inserters if s.kind == "out" and s.other == k}:
                outs = [s for s in inserters if s.kind == "out" and s.machine == m]
                rate = f.stages[machines[m][0]].rate
                for s in outs:
                    load[str(s.lane)] = load.get(str(s.lane), 0.0) + rate / len(outs)
            out_rows.append(
                BeltRow(
                    y,
                    "output",
                    tuple((ln, b.materials[0]) for ln in sorted(load)),  # type: ignore[misc]
                    tuple(sorted(load.items())),  # type: ignore[arg-type]
                    tiers[k],
                )
            )
    links: dict[tuple[int, int], int] = {}
    for s in inserters:
        if s.kind == "link":
            links[(s.other, s.machine)] = links.get((s.other, s.machine), 0) + 1
    meta = {
        "links": sorted([p, c, n] for (p, c), n in links.items()),
        "inserters": len(inserters),
        "poles": len(poles),
        "pipes": len(pipes or []),
    }
    return Period(
        width,
        height,
        tuple(ps),
        tuple(out_rows),
        spec,
        tuple(stage_of),
        meta,
        tuple(crossing_rows),
        tuple(fluid_of),
    )
