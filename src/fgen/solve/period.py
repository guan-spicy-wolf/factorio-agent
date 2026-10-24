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
    internal: dict[str, tuple[float, float]] = {}
    for item in made:
        demand = sum(
            sf.internal_in.get(item, 0.0) * s.count for s, sf in zip(spec.stages, out, strict=True)
        )
        if demand:
            supply = sum(
                sf.rate * s.count
                for s, sf in zip(spec.stages, out, strict=True)
                if sf.product == item
            )
            if supply < demand - _EPS:
                raise ValueError(f"{item}: period makes {supply:g}/s but uses {demand:g}/s")
            internal[item] = (supply, demand)
    # actual rates: final stages at full speed, producers only as fast as consumed
    input_rate: dict[str, float] = {}
    output_rate: dict[str, float] = {}
    for s, sf in zip(spec.stages, out, strict=True):
        share = 1.0
        if not sf.final:
            supply, demand = internal[sf.product]
            share = demand / supply
        else:
            output_rate[sf.product] = output_rate.get(sf.product, 0.0) + sf.rate * s.count
        for k, v in sf.external_in.items():
            input_rate[k] = input_rate.get(k, 0.0) + v * s.count * share
    return CycleFlows(
        stages=tuple(out),
        inputs=tuple(sorted({k for sf in out for k in sf.external_in})),
        outputs=tuple(sorted(output_rate)),
        output_rate=output_rate,
        input_rate=input_rate,
        internal=internal,
        surplus={k: s / d - 1 for k, (s, d) in internal.items()},
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

    def materials(self) -> frozenset[str]:
        return frozenset(m for _, m in self.lanes)


@dataclass(frozen=True)
class Period:
    width: int
    height: int
    placements: tuple[Placement, ...]
    belts: tuple[BeltRow, ...]
    spec: CycleSpec
    stage_of: tuple[int | None, ...]  # per placement: stage index for machines
    meta: dict[str, Any] = field(default_factory=dict)

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
        for x in range(w):
            i = at.get(Tile(x, b.y))
            p = period.placements[i] if i is not None else None
            if p is None or p.name != period.spec.belt or p.direction != FLOW:
                problems.append(
                    f"belt row y={b.y}: tile x={x} is not a {FLOW.name} {period.spec.belt}"
                )
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


def ceil_div(a: float, b: float) -> int:
    return max(0, math.ceil(a / b - _EPS))


def center(gd: GameData, name: str, x: int, y: int, d: Direction = Direction.N) -> Vec2:
    return footprint.center_for_tile_anchor(gd, name, Tile(x, y), d)
