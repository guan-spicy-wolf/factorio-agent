"""Generator family 1: belt-fed row, smelting instance (plan §4.5).

Single side, N-oriented, flow east (rows derived from the inserter reach, here for a
1-tile inserter and a 2x2 furnace)::

    y=0  ore belt  >>>>>>        input interface at its first tile
    y=1  i P i . i P             input inserters + poles (service band)
    y=2  F F F F F F
    y=3  F F F F F F             furnaces, step = furnace width
    y=4  i P i . i P             output inserters + poles (service band)
    y=5  plate belt >>>>>        output interface at its last tile

Everything is derived from ``GameData``: machine count from the recipe and crafting
speed, row offsets from the inserter pickup/insert vectors, furnace step from the
footprint, pole rows and spacing from ``supply_area_distance`` and
``maximum_wire_distance`` (one row of poles if its supply reaches both service bands,
otherwise one row per band). ``sides=2`` mirrors the furnace part across a shared ore
belt.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.geom import belt as beltgeom
from fgen.geom import footprint, inserter, pole
from fgen.geom.vec import Direction, Tile
from fgen.layout import transform as tf
from fgen.layout.model import Interface, Layout, Placement, Unit
from fgen.layout.validate import Violation, validate_unit

_EPS = 1e-9
FLOW = Direction.E  # belts flow east in the canonical orientation
FACING = Direction.N  # inserters face N: pick up from the north, drop to the south


@dataclass(frozen=True)
class SmeltingRowParams:
    recipe: str = "iron-plate"
    furnace: str = "stone-furnace"
    target_rate: float = 7.5  # products per second
    belt: str = "transport-belt"
    inserter: str = "inserter"
    pole: str = "small-electric-pole"
    sides: int = 1  # 1: single side; 2: furnaces on both sides of a shared ore belt
    fuel_lane: bool = True  # burner furnaces: ore on one lane, fuel on the other
    fuel: str = "coal"


@dataclass(frozen=True)
class _Plan:
    """Numbers derived from the parameters before any geometry."""

    rate: float  # product/s per furnace
    per_side: int
    total: int
    burner: bool
    ore_lanes: int
    ingredient: str
    ingredient_rate: float  # per furnace
    fuel_rate: float  # per furnace
    lane_cap: float
    inserter_cap: float
    problems: tuple[str, ...]


def _plan(p: SmeltingRowParams, gd: GameData) -> _Plan:
    rate = tp.machine_rate(gd, p.recipe, p.furnace)
    n = tp.machines_needed(gd, p.recipe, p.furnace, p.target_rate)
    per_side = math.ceil(n / p.sides)
    total = per_side * p.sides
    furnace = gd.entity(p.furnace)
    burner = furnace.is_burner
    (ingredient, ing_rate), *rest = tp.ingredient_rates(gd, p.recipe, p.furnace).items()
    fuel_rate = tp.fuel_rate(gd, p.furnace, p.fuel) if burner else 0.0
    lane_cap = tp.belt_lane_capacity(gd, p.belt)
    ins_cap = tp.inserter_throughput_estimate(gd, p.inserter)
    ore_lanes = 1 if p.fuel_lane else int(gd.constant("belt_lanes"))
    problems: list[str] = []
    if rest:
        problems.append(f"{p.recipe} has {1 + len(rest)} ingredients; this family feeds one")
    if burner and not p.fuel_lane:
        problems.append(f"{p.furnace} burns fuel but fuel_lane=False leaves no lane for it")
    ore = total * ing_rate
    if ore > lane_cap * ore_lanes + _EPS:
        problems.append(
            f"{total} furnaces need {ore:g} {ingredient}/s but {ore_lanes} lane(s) of "
            f"{p.belt} carry {lane_cap * ore_lanes:g}/s"
        )
    if burner and total * fuel_rate > lane_cap + _EPS:
        problems.append(f"fuel {total * fuel_rate:g}/s exceeds one lane ({lane_cap:g}/s)")
    side_out = per_side * rate
    if side_out > lane_cap + _EPS:
        problems.append(
            f"each output belt gets {side_out:g}/s on its far lane but a {p.belt} lane "
            f"carries {lane_cap:g}/s"
        )
    if ing_rate + fuel_rate > ins_cap + _EPS:
        problems.append(
            f"input {p.inserter} moves ~{ins_cap:g}/s, a furnace needs {ing_rate + fuel_rate:g}/s"
        )
    if rate > ins_cap + _EPS:
        problems.append(f"output {p.inserter} moves ~{ins_cap:g}/s, a furnace makes {rate:g}/s")
    return _Plan(
        rate=rate,
        per_side=per_side,
        total=total,
        burner=burner,
        ore_lanes=ore_lanes,
        ingredient=ingredient,
        ingredient_rate=ing_rate,
        fuel_rate=fuel_rate,
        lane_cap=lane_cap,
        inserter_cap=ins_cap,
        problems=tuple(problems),
    )


def _names_of_type(gd: GameData, type_: str) -> list[str]:
    return sorted(n for n, e in gd.entities.items() if e.type == type_)


def _check_names(p: SmeltingRowParams, gd: GameData) -> None:
    def need(name: str, type_: str, field: str) -> None:
        e = gd.entities.get(name)
        if e is None or e.type != type_:
            opts = ", ".join(f"{field}={o!r}" for o in _names_of_type(gd, type_))
            raise Infeasible(f"{field}={name!r} is not a {type_}", [opts] if opts else [])

    if p.recipe not in gd.recipes:
        raise Infeasible(f"unknown recipe {p.recipe!r}")
    need(p.furnace, "furnace", "furnace")
    need(p.belt, "transport-belt", "belt")
    need(p.inserter, "inserter", "inserter")
    need(p.pole, "electric-pole", "pole")
    if p.sides not in (1, 2):
        raise Infeasible(f"sides={p.sides} not supported", ["sides=1", "sides=2"])
    if not p.target_rate > 0:
        raise Infeasible("target_rate must be positive")
    cats = set(gd.recipe(p.recipe).categories)
    if not cats & set(gd.entity(p.furnace).crafting_categories):
        ok = [
            n for n in _names_of_type(gd, "furnace") if cats & set(gd.entity(n).crafting_categories)
        ]
        raise Infeasible(f"{p.furnace} cannot craft {p.recipe}", [f"furnace={n!r}" for n in ok])
    if gd.entity(p.inserter).is_burner:
        raise Infeasible(
            "burner inserters on the output side would never receive fuel",
            [f"inserter={n!r}" for n in _names_of_type(gd, "inserter") if gd.entity(n).is_electric],
        )


def _fmt(change: dict[str, Any]) -> str:
    return ", ".join(
        f"{k}={v:g}" if isinstance(v, float) else f"{k}={v!r}" for k, v in change.items()
    )


def _candidate_changes(p: SmeltingRowParams, gd: GameData) -> list[dict[str, Any]]:
    """All combinations of upgrades along independent axes, fewest changes first."""

    def faster(type_: str, current: str, key: Any) -> list[str]:
        return sorted(
            (n for n in _names_of_type(gd, type_) if key(n) > key(current) + _EPS), key=key
        )

    belts = faster("transport-belt", p.belt, lambda n: beltgeom.items_per_second(gd, n))
    inserters = [
        n
        for n in faster("inserter", p.inserter, lambda n: tp.inserter_throughput_estimate(gd, n))
        if gd.entity(n).is_electric
    ]
    categories = set(gd.recipe(p.recipe).categories)
    furnace_opts: list[dict[str, Any]] = [{}]
    if p.fuel_lane and not gd.entity(p.furnace).is_burner:
        furnace_opts.append({"fuel_lane": False})
    if not p.fuel_lane and gd.entity(p.furnace).is_burner:
        furnace_opts.append({"fuel_lane": True})
    for n in _names_of_type(gd, "furnace"):
        e = gd.entity(n)
        if n != p.furnace and e.is_electric and categories & set(e.crafting_categories):
            furnace_opts.append({"furnace": n, "fuel_lane": False})
    axes: list[list[dict[str, Any]]] = [
        [{}] + ([{"sides": 2}] if p.sides == 1 else []),
        [{}] + [{"belt": b} for b in belts],
        furnace_opts,
        [{}] + [{"inserter": i} for i in inserters],
    ]
    combos: list[dict[str, Any]] = [{}]
    for axis in axes:
        combos = [{**c, **o} for c in combos for o in axis]
    combos = [c for c in combos if c]
    combos.sort(key=len)
    return combos


def _max_rate(p: SmeltingRowParams, gd: GameData) -> float | None:
    """Largest target_rate that passes the capacity checks with the other params fixed."""
    rate = tp.machine_rate(gd, p.recipe, p.furnace)
    lo, best = 0, None
    hi = 10_000
    while lo < hi:  # number of furnaces, monotone in feasibility
        mid = (lo + hi + 1) // 2
        if not _plan(replace(p, target_rate=mid * rate), gd).problems:
            lo, best = mid, mid * rate
        else:
            hi = mid - 1
    return best


def suggestion_changes(p: SmeltingRowParams, gd: GameData, limit: int = 6) -> list[dict[str, Any]]:
    """Minimal parameter changes that make the capacity checks pass, fewest first."""
    feasible: list[dict[str, Any]] = []
    for change in _candidate_changes(p, gd):
        # skip supersets of an already feasible (smaller) change
        if any(f.items() <= change.items() for f in feasible):
            continue
        try:
            if not _plan(replace(p, **change), gd).problems:
                feasible.append(change)
        except ValueError:
            continue
    out = feasible[:limit]
    best = _max_rate(p, gd)
    if best is not None and best < p.target_rate:
        out.append({"target_rate": best})
    return out


def suggestions(p: SmeltingRowParams, gd: GameData, limit: int = 6) -> list[str]:
    return [_fmt(c) for c in suggestion_changes(p, gd, limit)]


# ---- geometry -------------------------------------------------------------------


def _pole_layout(
    gd: GameData,
    pole_name: str,
    consumers: list[frozenset[Tile]],
    occupied: set[Tile],
    bands: list[list[int]],
    x_range: tuple[int, int],
) -> list[Placement]:
    """Choose pole rows and columns so every consumer is supplied and poles connect.

    Tries a single pole row first (any row in any band whose supply reaches all
    consumers vertically), then one row per band. Within a row, a greedy sweep puts each
    pole as far right as possible while still covering the leftmost unsupplied consumer
    and staying within wire reach of the previous pole.
    """
    pw, ph = footprint.size(gd, pole_name)
    # supply offsets relative to the pole's anchor tile (translation invariant)
    c0 = footprint.center_for_tile_anchor(gd, pole_name, Tile(0, 0))
    sx0, sy0, sx1, sy1 = pole.supply_range(gd, pole_name, c0)

    def fits(x: int, y: int) -> bool:
        return all(Tile(x + i, y + j) not in occupied for i in range(pw) for j in range(ph))

    def rows_in(band: list[int]) -> list[int]:
        return [y for y in band if y + ph - 1 in band]

    def vertical(c: frozenset[Tile], y: int) -> list[int]:
        return [t.x for t in c if y + sy0 <= t.y <= y + sy1]

    def sweep(y: int, cons: list[frozenset[Tile]]) -> list[int] | None:
        spans = []
        for c in cons:
            xs = vertical(c, y)
            if not xs:
                return None
            spans.append((min(xs), max(xs)))
        spans.sort(key=lambda s: s[1])
        xs_free = [x for x in range(x_range[0], x_range[1] - pw + 2) if fits(x, y)]
        placed: list[int] = []
        covered = [False] * len(spans)
        while not all(covered):
            u = covered.index(False)
            hi_u = spans[u][1]
            limit = hi_u - sx0  # rightmost anchor whose supply still reaches hi_u
            cands = [x for x in xs_free if x <= limit and (not placed or x > placed[-1])]
            if placed:
                prev = footprint.center_for_tile_anchor(gd, pole_name, Tile(placed[-1], y))
                cands = [
                    x
                    for x in cands
                    if pole.wire_reaches(
                        gd,
                        pole_name,
                        prev,
                        pole_name,
                        footprint.center_for_tile_anchor(gd, pole_name, Tile(x, y)),
                    )
                ]
            if not cands:
                return None
            x = cands[-1]
            placed.append(x)
            for k, (a, b) in enumerate(spans):
                if a <= x + sx1 and b >= x + sx0:
                    covered[k] = True
        return placed

    def connected(poles: list[Placement]) -> bool:
        seen, stack = {0}, [0]
        while stack:
            a = stack.pop()
            for b in range(len(poles)):
                if b not in seen and pole.wire_reaches(
                    gd, pole_name, poles[a].position, pole_name, poles[b].position
                ):
                    seen.add(b)
                    stack.append(b)
        return len(seen) == len(poles)

    def build(rows: list[tuple[int, list[int]]]) -> list[Placement] | None:
        out = []
        for y, xs in rows:
            out += [
                Placement(pole_name, footprint.center_for_tile_anchor(gd, pole_name, Tile(x, y)))
                for x in xs
            ]
        return out if out and connected(out) else None

    if not consumers:
        return []
    best: list[Placement] | None = None
    for band in bands:
        for y in rows_in(band):
            xs = sweep(y, consumers)
            if xs is not None:
                cand = build([(y, xs)])
                if cand and (best is None or len(cand) < len(best)):
                    best = cand
    if best is not None:
        return best
    # one pole row per band; each consumer goes to the band row that reaches it
    if len(bands) == 2:
        for y_a in rows_in(bands[0]):
            for y_b in rows_in(bands[1]):
                cons_a = [c for c in consumers if vertical(c, y_a)]
                cons_b = [c for c in consumers if not vertical(c, y_a)]
                xs_a = sweep(y_a, cons_a) if cons_a else []
                xs_b = sweep(y_b, cons_b) if cons_b else []
                if xs_a is None or xs_b is None:
                    continue
                cand = build([(y_a, xs_a), (y_b, xs_b)])
                if cand and (best is None or len(cand) < len(best)):
                    best = cand
    if best is None:
        raise Infeasible(f"cannot supply every consumer with {pole_name} in the service rows")
    return best


def _half(p: SmeltingRowParams, gd: GameData, plan: _Plan) -> tuple[Unit, int]:
    """One side, flow east, ore belt on row 0. Returns the unit and the ore-belt row."""
    fw, fh = footprint.size(gd, p.furnace, FACING)
    bw, bh = footprint.size(gd, p.belt, FLOW)
    iw, ih = footprint.size(gd, p.inserter, FACING)
    if (bw, bh, iw, ih) != (1, 1, 1, 1):
        raise Infeasible("this family assumes 1x1 belts and inserters")
    pu, dr = inserter.reach_offsets(gd, p.inserter)
    if pu.x != 0 or dr.x != 0 or not pu.y < 0 < dr.y:
        raise Infeasible(f"{p.inserter} does not move items straight across its tile")

    y_belt_in = 0
    y_ins_in = y_belt_in - pu.y
    y_furn = y_ins_in + dr.y
    y_furn_bottom = y_furn + fh - 1
    y_ins_out = y_furn_bottom - pu.y
    y_belt_out = y_ins_out + dr.y
    length = plan.per_side * fw
    col = (fw - 1) // 2  # inserter column inside each furnace span

    placements: list[Placement] = []

    def put(name: str, x: int, y: int, d: Direction) -> None:
        placements.append(
            Placement(name, footprint.center_for_tile_anchor(gd, name, Tile(x, y), d), d)
        )

    for x in range(length):
        put(p.belt, x, y_belt_in, FLOW)
    for k in range(plan.per_side):
        x0 = k * fw
        put(p.furnace, x0, y_furn, FACING)
        put(p.inserter, x0 + col, y_ins_in, FACING)
        put(p.inserter, x0 + col, y_ins_out, FACING)
    for x in range(length):
        put(p.belt, x, y_belt_out, FLOW)

    occupied: set[Tile] = set()
    for pl in placements:
        occupied |= pl.tiles(gd)
    consumers = [pl.tiles(gd) for pl in placements if gd.entity(pl.name).is_electric]
    bands = [
        list(range(y_belt_in + 1, y_furn)),
        list(range(y_furn_bottom + 1, y_belt_out)),
    ]
    poles = _pole_layout(gd, p.pole, consumers, occupied, bands, (0, length - 1))
    placements += poles

    product, _ = tp.main_product(gd, p.recipe)
    out_lane = beltgeom.drop_lane(FLOW, FACING.opposite)  # arm moves south onto the belt
    ifaces: list[Interface] = []
    ore_lane = "left" if p.fuel_lane else "both"
    feed_rate = plan.total * plan.ingredient_rate
    ifaces.append(
        Interface(
            "item",
            plan.ingredient,
            Tile(0, y_belt_in),
            FLOW,
            ore_lane,
            feed_rate,
            "input",
            [
                f"{p.belt} enters from the west, {plan.ingredient} on the {ore_lane} "
                f"lane{'s' if ore_lane == 'both' else ''}, compressed enough for "
                f"{feed_rate:g}/s"
            ],
        )
    )
    if p.fuel_lane:
        fuel_assumptions = (
            [f"{p.fuel} on the right lane, at least {plan.total * plan.fuel_rate:g}/s"]
            if plan.burner
            else [f"lane reserved for fuel but unused: {p.furnace} is electric"]
        )
        ifaces.append(
            Interface(
                "item",
                p.fuel if plan.burner else None,
                Tile(0, y_belt_in),
                FLOW,
                "right",
                plan.total * plan.fuel_rate if plan.burner else 0.0,
                "input",
                fuel_assumptions,
            )
        )
    ifaces.append(
        Interface(
            "item",
            product,
            Tile(length - 1, y_belt_out),
            FLOW,
            out_lane,
            plan.per_side * plan.rate,
            "output",
            [f"{product} on the {out_lane} (far) lane only; downstream must keep it moving"],
        )
    )
    if poles:
        first = min(poles, key=lambda q: (q.position.x, q.position.y))
        ifaces.append(
            Interface(
                "power",
                None,
                first.position.floor(),
                FLOW,
                None,
                None,
                "input",
                [f"connect a {p.pole}-compatible wire to this pole"],
            )
        )
    meta = {"belt_terminals": [Tile(length - 1, y_belt_in)]}
    return Unit(Layout(placements), ifaces, meta), y_belt_in


def _pole_alternatives(p: SmeltingRowParams, gd: GameData, plan: _Plan) -> list[dict[str, Any]]:
    out = []
    for name in _names_of_type(gd, "electric-pole"):
        if name == p.pole:
            continue
        try:
            _half(replace(p, pole=name), gd, plan)
        except Infeasible:
            continue
        out.append({"pole": name})
    return out


class SmeltingRow:
    family = "belt-fed-row/smelting"

    def generate(self, params: SmeltingRowParams, gd: GameData) -> Unit:
        p = params
        _check_names(p, gd)
        plan = _plan(p, gd)
        if plan.problems:
            changes = suggestion_changes(p, gd)
            raise Infeasible("; ".join(plan.problems), [_fmt(c) for c in changes], changes)
        try:
            half, y_in = _half(p, gd, plan)
        except Infeasible as exc:
            if exc.suggestions:
                raise
            alts = _pole_alternatives(p, gd, plan)
            raise Infeasible(exc.reason, [_fmt(c) for c in alts], alts) from None
        unit = half
        if p.sides == 2:
            # mirror everything except the shared ore belt across the ore-belt row
            rest = Unit(
                Layout(
                    [
                        q
                        for q in half.layout.placements
                        if not (q.name == p.belt and q.position.floor().y == y_in)
                    ]
                ),
                [i for i in half.interfaces if i.role == "output" or i.kind == "power"],
                {},
            )
            mirrored = tf.translate(tf.mirror_y(rest), 0, 2 * y_in + 1)
            unit = Unit(
                Layout(half.layout.placements + mirrored.layout.placements),
                half.interfaces + [i for i in mirrored.interfaces if i.kind != "power"],
                dict(half.meta),
            )
        lo, _ = unit.layout.bounds(gd)
        unit = tf.translate(unit, -lo.x, -lo.y)
        meta = {
            "family": self.family,
            "params": asdict(p),
            "recipe": p.recipe,
            "machine": p.furnace,
            "fuel": p.fuel if plan.burner else None,
            "target_rate": p.target_rate,
            "machines_per_side": plan.per_side,
            "belt_terminals": [t.translate(-lo.x, -lo.y) for t in half.meta["belt_terminals"]],
            "icon": tp.main_product(gd, p.recipe)[0],
        }
        unit = Unit(unit.layout, unit.interfaces, meta)
        violations = self.invariants(unit, gd)
        if violations:
            detail = "; ".join(f"{v.code}: {v.message}" for v in violations[:5])
            raise Infeasible(f"generated layout violates invariants: {detail}")
        return unit

    def invariants(self, unit: Unit, gd: GameData) -> list[Violation]:
        return validate_unit(unit, gd)


def generate(params: SmeltingRowParams, gd: GameData) -> Unit:
    return SmeltingRow().generate(params, gd)
