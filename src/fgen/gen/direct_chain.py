"""Generator family 3: direct-insertion chain (plan-phase4a §1.2).

A recipe whose ingredient the same machine can also craft (electronic circuit <- copper
cable) is built as a row of **periods**: each period holds ``a`` producer machines and
``b`` consumer machines, the intermediate goes machine to machine through inserters
and never touches a belt, the raw materials come in on belts and the product leaves on
a belt. The period is the leaf: :func:`fgen.solve.cycle.solve` searches it (or
:func:`fgen.gen.direct_chain_baseline.baseline_period` writes the classic one by hand,
``layout="baseline"``), the unit is ``k`` copies along the belts.

Numbers (all from ``GameData``):

- the exact ratio ``a : b`` from crafting speeds and recipe amounts
  (:func:`fgen.solve.period.ratio`, 3:2 for circuits); a period may instead use
  ``b' < b`` consumers with ``a' = ceil(b' * a / b)`` producers (slight surplus of the
  intermediate, written into ``assumptions``);
- the period type and count ``k``: fewest machines with ``k * b' * rate >= target``;
- lane capacity for ``k`` periods sharing each lane: passed to the solver
  (``repeats``), so a mixed belt is only chosen while its lanes keep up.

Contract (phase 6a fields): one input port per raw material at the west end of its belt
(lane, per-lane rate, ``MinRate`` for the target, the belt's lane materials on its port
group), one output port at the east end of the product belt, one power port (first
pole). Input belts end at ``belt_terminals``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from typing import Any, Literal

from fgen.data.runtime import NO_RESEARCH, RuntimeState
from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.geom import belt as beltgeom
from fgen.geom import inserter
from fgen.geom.vec import Tile
from fgen.layout.model import (
    Interface,
    LaneMaterial,
    Layout,
    MinRate,
    PortGroup,
    PortSpace,
    Predicate,
    RateProfile,
    Unit,
)
from fgen.layout.validate import Violation, validate_unit
from fgen.solve import cpsat, cycle
from fgen.solve.period import FLOW, CycleSpec, Period, Stage, check_period, flows, tile

_EPS = 1e-9
MAX_CONSUMERS = 4  # search-size bound on consumers per period (not a game number)
BACKENDS = ("exhaustive", "cpsat")
MAX_LANE_CONSUMERS = 12  # search-size bound for lane periods (not a game number)


@dataclass(frozen=True)
class DirectChainParams:
    recipe: str = "electronic-circuit"
    target_rate: float = 2.0  # products per second
    machine: str = "assembling-machine-1"
    inserter: str = "fast-inserter"
    belt: str = "transport-belt"
    pole: str = "small-electric-pole"
    extra_inserters: tuple[str, ...] = ()  # solver: further tiers it may mix in
    layout: Literal["solver", "baseline"] = "solver"
    time_limit: float = 60.0  # solver, seconds per period
    backend: Literal["exhaustive", "cpsat"] = "exhaustive"  # solver backend (plan-phase4b)
    belt_inputs: tuple[str, ...] = ()  # craftable ingredients that come on belts anyway
    max_consumers: int = MAX_CONSUMERS  # final machines per period at most (search size)
    intermediates: Literal["auto", "direct", "lane"] = "auto"  # period kinds to consider
    max_lane_consumers: int = MAX_LANE_CONSUMERS  # lane periods: final machines at most


@dataclass(frozen=True)
class ChainPlan:
    spec: CycleSpec  # one period
    periods: int  # k
    exact: tuple[int, int]  # exact producer : consumer ratio
    rate: float  # product per period
    product: str
    intermediate: str  # the first intermediate (the only one for one-level chains)
    intermediates: tuple[str, ...] = ()


def _names_of_type(gd: GameData, type_: str) -> list[str]:
    return sorted(n for n, e in gd.entities.items() if e.type == type_)


def _check_names(p: DirectChainParams, gd: GameData) -> None:
    def need(name: str, type_: str, field: str) -> None:
        e = gd.entities.get(name)
        if e is None or e.type != type_:
            opts = ", ".join(f"{field}={o!r}" for o in _names_of_type(gd, type_))
            raise Infeasible(f"{field}={name!r} is not a {type_}", [opts] if opts else [])

    if p.recipe not in gd.recipes:
        raise Infeasible(f"unknown recipe {p.recipe!r}")
    need(p.machine, "assembling-machine", "machine")
    need(p.belt, "transport-belt", "belt")
    need(p.inserter, "inserter", "inserter")
    need(p.pole, "electric-pole", "pole")
    for n in p.extra_inserters:
        need(n, "inserter", "extra_inserters")
    if not p.target_rate > 0:
        raise Infeasible("target_rate must be positive")
    if not set(gd.recipe(p.recipe).categories) & set(gd.entity(p.machine).crafting_categories):
        raise Infeasible(f"{p.machine} cannot craft {p.recipe}")
    if not all(gd.entity(n).is_electric for n in (p.inserter, *p.extra_inserters)):
        raise Infeasible(
            "inserters between machines would never receive fuel",
            [f"inserter={n!r}" for n in _names_of_type(gd, "inserter") if gd.entity(n).is_electric],
        )
    if p.layout not in ("solver", "baseline"):
        raise Infeasible(f"layout={p.layout!r}", ["layout='solver'", "layout='baseline'"])
    if p.backend not in BACKENDS:
        raise Infeasible(f"backend={p.backend!r}", [f"backend={b!r}" for b in BACKENDS])
    if p.layout == "solver" and p.backend == "cpsat" and not cpsat.available():
        raise Infeasible(cpsat.INSTALL_HINT, ["backend='exhaustive'"], [{"backend": "exhaustive"}])


def _producer(gd: GameData, item: str, machine: str, exclude: str) -> str | None:
    cats = set(gd.entity(machine).crafting_categories)
    names = sorted(
        r
        for r, rp in gd.recipes.items()
        if r != exclude and rp.results and rp.results[0][0] == item and set(rp.categories) & cats
    )
    return names[0] if names else None


def tree(p: DirectChainParams, gd: GameData) -> list[tuple[str, str]]:
    """``(item, recipe)`` for every ingredient (at any depth) the machine can craft,
    except ``belt_inputs``: the intermediates the chain makes itself."""
    raw = set(p.belt_inputs)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    queue = [p.recipe]
    while queue:
        for n, _ in gd.recipe(queue.pop(0)).ingredients:
            if n in raw or n in seen:
                continue
            r = _producer(gd, n, p.machine, p.recipe)
            if r is not None:
                seen.add(n)
                out.append((n, r))
                queue.append(r)
    return out


def exact_counts(p: DirectChainParams, gd: GameData) -> dict[str, Fraction]:
    """Machines of each intermediate recipe per final machine, both at full speed
    (copper cable per circuit machine: 3/2)."""
    inter = tree(p, gd)
    q: dict[str, Fraction] = {p.recipe: Fraction(1)}
    pending = list(inter)
    while pending:
        for item, r in list(pending):
            users = [rr for rr in q if item in dict(gd.recipe(rr).ingredients)]
            if any(item in dict(gd.recipe(r2).ingredients) and r2 not in q for _, r2 in pending):
                continue  # a consumer of this item has no count yet
            need = sum(
                (q[u] * Fraction(tp.ingredient_rates(gd, u, p.machine)[item]) for u in users),
                Fraction(0),
            )
            q[r] = (need / Fraction(tp.machine_rate(gd, r, p.machine))).limit_denominator(1000)
            pending.remove((item, r))
    return {r: v for r, v in q.items() if r != p.recipe}


def plans(p: DirectChainParams, gd: GameData) -> list[ChainPlan]:
    """Every period type with the count ``k`` that meets the target, fewest machines
    first (ties: direct insertion before lanes, then closer to the exact ratio).

    A type is ``b'`` final machines plus ``ceil(b' * exact)`` machines per intermediate
    recipe, of one of these kinds: **direct** (intermediates machine to machine, ``b' <=
    max_consumers``) or **lane** (intermediates dropped onto a lane of an input belt
    upstream of their consumers, ``b' <= max_lane_consumers``; the fallback when one
    producer would have to reach more consumers than inserters can): first only the
    final recipe's own ingredients on lanes, then every intermediate. ``intermediates``
    restricts the kinds."""
    _check_names(p, gd)
    inter = tree(p, gd)
    if not inter:
        raise Infeasible(
            f"no ingredient of {p.recipe} can be made by {p.machine}: nothing to chain"
        )
    q = exact_counts(p, gd)
    rate = tp.machine_rate(gd, p.recipe, p.machine)
    product = tp.main_product(gd, p.recipe)[0]
    den = math.lcm(*(v.denominator for v in q.values()))
    kinds: list[tuple[int, tuple[str, ...], int]] = []  # (rank, lane items, most b')
    if p.intermediates in ("auto", "direct"):
        kinds.append((0, (), min(den, p.max_consumers)))
    if p.intermediates in ("auto", "lane"):
        # the final recipe's own intermediates on lanes (deeper ones machine to machine),
        # then every intermediate on a lane
        final_ings = {n for n, _ in gd.recipe(p.recipe).ingredients}
        for lane_items in (
            tuple(i for i, _ in inter if i in final_ings),
            tuple(i for i, _ in inter),
        ):
            if lane_items and all(lane_items != li for _, li, _ in kinds):
                kinds.append((len(kinds), lane_items, min(den, p.max_lane_consumers)))
    options = []
    for rank, lane_items, most in kinds:
        for b in range(1, most + 1):
            counts = {r: math.ceil(b * v - _EPS) for r, v in q.items()}
            k = max(1, math.ceil(p.target_rate / (b * rate) - _EPS))
            spec = CycleSpec(
                (
                    Stage(p.recipe, p.machine, b),
                    *(Stage(r, p.machine, counts[r]) for _, r in inter),
                ),
                p.inserter,
                p.pole,
                p.belt,
                tuple(p.extra_inserters),
                lane_items,
            )
            exact: tuple[int, ...]
            if len(inter) == 1:
                v = q[inter[0][1]]
                exact = (v.numerator, v.denominator)
            else:
                exact = (*(int(x * den) for x in q.values()), den)
            options.append(
                (
                    (k * (b + sum(counts.values())), rank, -b),
                    ChainPlan(
                        spec, k, exact, b * rate, product, inter[0][0], tuple(i for i, _ in inter)
                    ),
                )
            )
    return [pl for _, pl in sorted(options, key=lambda o: o[0])]


def _tag(spec: CycleSpec) -> str:
    """Machine counts, intermediates first, the final stage last (``3:2``)."""
    return ":".join(str(st.count) for st in (*spec.stages[1:], spec.stages[0]))


def plan(p: DirectChainParams, gd: GameData) -> ChainPlan:
    """The preferred period type (see :func:`plans`)."""
    return plans(p, gd)[0]


def _capacity_problems(pl: ChainPlan, p: DirectChainParams, gd: GameData) -> list[str]:
    """Rates one belt cannot carry whatever the period looks like."""
    f = flows(pl.spec, gd)
    full = beltgeom.items_per_second(gd, p.belt)
    out = []
    for m, r in sorted({**f.input_rate, **f.output_rate}.items()):
        if r * pl.periods > full + _EPS:
            out.append(f"{m} {r * pl.periods:g}/s exceeds one {p.belt} ({full:g}/s)")
    return out


def lane_repeats(per: Period, gd: GameData) -> int:
    """How many copies of ``per`` one belt's lanes carry (the period's own lane loads)."""
    cap = tp.belt_lane_capacity(gd, per.spec.belt)
    k = 10**6
    for b in per.belts:
        for _, v in b.rates:
            if v > _EPS:
                k = min(k, math.floor(cap / v + _EPS))
    return k


def _belt_suggestions(p: DirectChainParams, gd: GameData) -> list[dict[str, Any]]:
    """A faster belt that carries the target, and the largest target this belt carries
    with the single-period layouts the solver finds."""
    cur = beltgeom.items_per_second(gd, p.belt)
    faster = sorted(
        (n for n in _names_of_type(gd, "transport-belt") if beltgeom.items_per_second(gd, n) > cur),
        key=lambda n: beltgeom.items_per_second(gd, n),
    )
    out: list[dict[str, Any]] = []
    for n in faster:
        q = replace(p, belt=n)
        pl = plan(q, gd)
        if _capacity_problems(pl, q, gd):
            continue
        try:
            res = solve_period(pl.spec, gd, 1, p.time_limit, p.backend)
        except Infeasible:
            continue
        if res.best is not None and lane_repeats(res.best, gd) >= pl.periods:
            out.append({"belt": n})
            break
    best = None
    for pl in plans(replace(p, target_rate=1e-9), gd):
        if p.layout == "baseline":
            from fgen.gen.direct_chain_baseline import baseline_period

            try:
                per: Period | None = baseline_period(pl.spec, gd)
            except Infeasible:
                per = None
        else:
            try:
                per = solve_period(pl.spec, gd, 1, p.time_limit, p.backend).best
            except Infeasible:
                per = None
        if per is not None:
            r = lane_repeats(per, gd) * pl.rate
            best = r if best is None else max(best, r)
    if best is not None and best < p.target_rate - _EPS:
        out.append({"target_rate": best})
    return out


def _fmt(change: dict[str, Any]) -> str:
    return ", ".join(
        f"{k}={v:g}" if isinstance(v, float) else f"{k}={v!r}" for k, v in change.items()
    )


_SOLVED: dict[tuple[Any, ...], cycle.SolveResult] = {}


def solve_period(
    spec: CycleSpec, gd: GameData, repeats: int, time_limit: float, backend: str = "exhaustive"
) -> cycle.SolveResult:
    """The solver (``backend``: exhaustive search or CP-SAT, same interface), memoised
    per process (generators and sweeps call it repeatedly)."""
    if spec.lane_items and backend != "cpsat":
        raise Infeasible("lane intermediates need the CP-SAT backend", ["backend='cpsat'"])
    key = (spec, id(gd), repeats, time_limit, backend)
    if key not in _SOLVED:
        solve = cpsat.solve if backend == "cpsat" else cycle.solve
        _SOLVED[key] = solve(spec, gd, repeats=repeats, time_limit=time_limit)
    return _SOLVED[key]


def _solver_info(res: cycle.SolveResult, repeats: int, backend: str) -> dict[str, Any]:
    st = res.stats
    return {
        "layout": "solver",
        "backend": backend,
        "solved_for_repeats": repeats,
        "seconds": round(st.seconds, 3),
        "timed_out": st.timed_out,
        "nodes": dict(st.nodes),
        "pruned": dict(st.pruned),
        "variables": dict(st.variables),
        "sizes_tried": len(st.sizes),
        "candidates": [
            {
                "width": c.width,
                "height": c.height,
                "area": c.area,
                "poles": c.meta["poles"],
                "inserters": c.meta["inserters"],
            }
            for c in res.periods
        ],
    }


def period_for(pl: ChainPlan, p: DirectChainParams, gd: GameData) -> tuple[Period, dict[str, Any]]:
    """The period for ``pl``: hand-written (``layout="baseline"``) or solved. The solver
    first runs for one period; only when ``k`` copies of that would overload a lane is
    it run again with the lanes checked for ``k`` (``repeats``), which can only drop
    candidates, so a fitting single-period answer is also the smallest for ``k``."""
    if p.layout == "baseline":
        from fgen.gen.direct_chain_baseline import baseline_period

        per = baseline_period(pl.spec, gd)
        if lane_repeats(per, gd) < pl.periods:
            raise Infeasible(f"{pl.periods} baseline periods overload a {p.belt} lane")
        return per, {"layout": "baseline"}
    res = solve_period(pl.spec, gd, 1, p.time_limit, p.backend)
    reps = 1
    if res.best is not None and lane_repeats(res.best, gd) < pl.periods:
        res = solve_period(pl.spec, gd, pl.periods, p.time_limit, p.backend)
        reps = pl.periods
    if res.best is None:
        st = res.stats
        why = "timed out" if st.timed_out else "no period within the search bounds"
        raise Infeasible(
            f"solver found no {pl.spec.machines()}-machine period ({why}; {st.summary()})"
        )
    return res.best, _solver_info(res, reps, p.backend)


def _lane(lanes: list[str]) -> Any:
    return lanes[0] if len(lanes) == 1 else "both"


def build_unit(per: Period, pl: ChainPlan, p: DirectChainParams, gd: GameData) -> Unit:
    k, w = pl.periods, per.width
    layout: Layout = tile(per, k)
    f = flows(pl.spec, gd)
    ifaces: list[Interface] = []
    groups: list[PortGroup] = []
    terminals: list[Tile] = []
    target = p.target_rate
    scale = target / (pl.rate * k)  # share of the chain's capacity the target needs
    for b in per.belts:
        g = f"{b.role}-y{b.y}"
        lanes = dict(b.rates)
        if b.role == "input":
            terminals.append(Tile(k * w - 1, b.y))
            mats = sorted(set(b.materials()))
            reqs: list[Predicate] = []
            if len(mats) > 1:
                reqs = [LaneMaterial(ln, m) for ln, m in b.lanes]  # type: ignore[arg-type]
            groups.append(PortGroup(g, tuple(reqs)))
            for m in mats:
                if m in pl.spec.lane_items:
                    continue  # made inside the period: the feeder leaves this lane empty
                lns = sorted(ln for ln, mm in b.lanes if mm == m)
                rates = {ln: lanes[ln] * k for ln in lns}
                total = sum(rates.values())
                ifaces.append(
                    Interface(
                        "item",
                        m,
                        Tile(0, b.y),
                        FLOW,
                        _lane(lns),
                        total,
                        "input",
                        [f"{p.belt} enters from the west, {m} on lane(s) {'/'.join(lns)}"],
                        rate_profile=RateProfile.fixed(total, rates),  # type: ignore[arg-type]
                        requires=(MinRate(total * scale),),
                        entity=p.belt,
                        port_group=g,
                        space=PortSpace.item("both"),
                    )
                )
        else:
            groups.append(PortGroup(g))
            lns = sorted(lanes)
            rates = {ln: lanes[ln] * k for ln in lns}
            total = sum(rates.values())
            note = [f"{pl.product} on lane(s) {'/'.join(lns)}; downstream must keep it moving"]
            for bb in per.belts:
                for ln, item in bb.lanes:
                    if item in pl.spec.lane_items:
                        note.append(
                            f"{item} rides the {ln} lane of the input belt at y={bb.y}: made "
                            "in every period upstream of its pickups; the feeder leaves that "
                            "lane empty, surplus backs it up to the belt end"
                        )
            for item, s in f.surplus.items():
                if s > _EPS:
                    sup, dem = f.internal[item]
                    note.append(
                        f"{item}: {sup:g}/s made per period, {dem:g}/s used (approximate "
                        f"{_tag(pl.spec)} ratio, exact {':'.join(map(str, pl.exact))}); "
                        "producers idle the rest"
                    )
            ifaces.append(
                Interface(
                    "item",
                    pl.product,
                    Tile(k * w - 1, b.y),
                    FLOW,
                    _lane(lns),
                    total,
                    "output",
                    note,
                    rate_profile=RateProfile.fixed(total, rates),  # type: ignore[arg-type]
                    entity=p.belt,
                    port_group=g,
                    space=PortSpace.item("direct"),
                )
            )
    poles = [q for q in layout.placements if gd.entity(q.name).type == "electric-pole"]
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
            entity=p.pole,
        )
    )
    meta = {
        "belt_terminals": terminals,
        "recipe": p.recipe,
        "machine": p.machine,
        "target_rate": p.target_rate,
        "icon": pl.product,
    }
    return Unit(layout, ifaces, meta, tuple(groups))


def predict(unit: Unit, gd: GameData, rs: RuntimeState = NO_RESEARCH) -> dict[str, Any]:
    """Analytic throughput of a chain from its contract and period meta; the output
    inserters' capacity (per final machine, capped at its rate) is counted under the
    force state ``rs`` (hand size bonus)."""
    m = unit.meta
    w, h = unit.layout.size(gd)
    rate = m["period"]["rate"] * m["periods"]
    limits = {"machines": rate}
    finals = [q for q in unit.layout.placements if q.extra.get("recipe") == m["recipe"]]
    machine_tiles = [q.tiles(gd) for q in unit.layout.placements if q.extra.get("recipe")]
    every = set().union(*machine_tiles) if machine_tiles else set()
    per_machine = tp.machine_rate(gd, m["recipe"], m["machine"])
    out_cap = 0.0
    for q in finals:
        ts = q.tiles(gd)
        cap = 0.0
        for e in unit.layout.placements:
            if gd.entity(e.name).type != "inserter":
                continue
            pu = inserter.pickup_tile(gd, e.name, e.position, e.direction)
            dr = inserter.drop_tile(gd, e.name, e.position, e.direction)
            if pu in ts and dr not in every:
                cap += tp.inserter_throughput_estimate(gd, e.name, rs)
        out_cap += min(per_machine, cap)
    limits["output_inserters"] = out_cap
    for i in unit.interfaces:
        if i.kind != "item" or i.role != "output" or i.entity is None:
            continue
        lanes = 1 if i.lane in ("left", "right") else int(gd.constant("belt_lanes"))
        limits["output_belt"] = tp.belt_lane_capacity(gd, i.entity) * lanes
    limits = {k: round(v, 9) for k, v in limits.items()}
    bottleneck = min(limits, key=lambda k: limits[k])
    machines = sum(
        1 for q in unit.layout.placements if gd.entity(q.name).type == "assembling-machine"
    )
    return {
        "area": w * h,
        "width": w,
        "height": h,
        "machines": machines,
        "throughput": limits[bottleneck],
        "bottleneck": bottleneck,
        "limits": limits,
        "target_rate": m["target_rate"],
        "product": m["icon"],
        "runtime": rs.to_json(),
    }


class DirectChain:
    family = "direct-chain"

    def generate(self, params: DirectChainParams, gd: GameData) -> Unit:
        p = params
        options = plans(p, gd)
        failures: list[str] = []
        found: tuple[ChainPlan, Period, dict[str, Any]] | None = None
        for pl in options:
            problems = _capacity_problems(pl, p, gd)
            if problems:
                failures.append(f"{_tag(pl.spec)}: " + "; ".join(problems))
                continue
            try:
                per, info = period_for(pl, p, gd)
            except Infeasible as exc:
                tag = f"{_tag(pl.spec)} x{pl.periods}"
                failures.append(f"{tag}: {exc.reason}")
                continue
            found = (pl, per, info)
            break
        if found is None:
            changes = _belt_suggestions(p, gd)
            if p.backend != "cpsat" and any(pl.spec.lane_items for pl in options):
                changes.insert(0, {"backend": "cpsat"})
            raise Infeasible(" | ".join(failures), [_fmt(c) for c in changes], changes)
        pl, per, info = found
        info["rejected"] = failures
        bad = check_period(per, gd)
        if bad:
            raise Infeasible(f"period breaks its boundary conditions: {bad[:3]}")
        unit = build_unit(per, pl, p, gd)
        meta = {
            **unit.meta,
            "family": self.family,
            "params": asdict(p),
            "periods": pl.periods,
            "ratio": [*(st.count for st in pl.spec.stages[1:]), pl.spec.stages[0].count],
            "stages": [st.recipe for st in (*pl.spec.stages[1:], pl.spec.stages[0])],
            "exact_ratio": list(pl.exact),
            "intermediate": pl.intermediate,
            "intermediates": list(pl.intermediates),
            "period": {
                "width": per.width,
                "height": per.height,
                "area": per.area,
                "rate": pl.rate,
                "machines": pl.spec.machines(),
                "inserters": per.meta["inserters"],
                "poles": per.meta["poles"],
                "links": per.meta["links"],
                **info,
            },
        }
        unit = replace(unit, meta=meta)
        violations = self.invariants(unit, gd)
        if violations:
            detail = "; ".join(f"{v.code}: {v.message}" for v in violations[:5])
            raise Infeasible(f"generated layout violates invariants: {detail}")
        return unit

    def invariants(self, unit: Unit, gd: GameData) -> list[Violation]:
        return validate_unit(unit, gd)


def generate(params: DirectChainParams, gd: GameData) -> Unit:
    return DirectChain().generate(params, gd)
