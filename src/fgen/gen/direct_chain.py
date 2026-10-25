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
from typing import Any, Literal

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.geom import belt as beltgeom
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
from fgen.solve import cycle
from fgen.solve.period import FLOW, CycleSpec, Period, Stage, check_period, flows, ratio, tile

_EPS = 1e-9
MAX_CONSUMERS = 4  # search-size bound on consumers per period (not a game number)


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


@dataclass(frozen=True)
class ChainPlan:
    spec: CycleSpec  # one period
    periods: int  # k
    exact: tuple[int, int]  # exact producer : consumer ratio
    rate: float  # product per period
    product: str
    intermediate: str


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


def _producer(gd: GameData, item: str, machine: str, exclude: str) -> str | None:
    cats = set(gd.entity(machine).crafting_categories)
    names = sorted(
        r
        for r, rp in gd.recipes.items()
        if r != exclude and rp.results and rp.results[0][0] == item and set(rp.categories) & cats
    )
    return names[0] if names else None


def plans(p: DirectChainParams, gd: GameData) -> list[ChainPlan]:
    """Every period type (``a' : b'`` for ``b' = 1..b``) with the count ``k`` that meets
    the target, fewest machines first (ties: closer to the exact ratio)."""
    _check_names(p, gd)
    ings = [n for n, _ in gd.recipe(p.recipe).ingredients]
    made = {i: _producer(gd, i, p.machine, p.recipe) for i in ings}
    inter = [i for i, r in made.items() if r is not None]
    if not inter:
        raise Infeasible(
            f"no ingredient of {p.recipe} can be made by {p.machine}: nothing to chain"
        )
    if len(inter) > 1:
        raise Infeasible(f"{p.recipe}: {len(inter)} craftable ingredients; this family chains one")
    item = inter[0]
    prod_recipe = str(made[item])
    if any(_producer(gd, n, p.machine, prod_recipe) for n, _ in gd.recipe(prod_recipe).ingredients):
        raise Infeasible(f"{prod_recipe} has craftable ingredients itself; one level only")
    q = ratio(gd, (p.recipe, p.machine), (prod_recipe, p.machine))
    rate = tp.machine_rate(gd, p.recipe, p.machine)
    product = tp.main_product(gd, p.recipe)[0]
    options = []
    for b in range(1, min(q.denominator, MAX_CONSUMERS) + 1):
        a = math.ceil(b * q - _EPS)
        k = max(1, math.ceil(p.target_rate / (b * rate) - _EPS))
        spec = CycleSpec(
            (Stage(p.recipe, p.machine, b), Stage(prod_recipe, p.machine, a)),
            p.inserter,
            p.pole,
            p.belt,
            tuple(p.extra_inserters),
        )
        options.append(
            (
                (k * (a + b), -b),
                ChainPlan(spec, k, (q.numerator, q.denominator), b * rate, product, item),
            )
        )
    return [pl for _, pl in sorted(options, key=lambda o: o[0])]


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
        res = solve_period(pl.spec, gd, 1, p.time_limit)
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
            per = solve_period(pl.spec, gd, 1, p.time_limit).best
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
    spec: CycleSpec, gd: GameData, repeats: int, time_limit: float
) -> cycle.SolveResult:
    """The solver, memoised per process (generators and sweeps call it repeatedly)."""
    key = (spec, id(gd), repeats, time_limit)
    if key not in _SOLVED:
        _SOLVED[key] = cycle.solve(spec, gd, repeats=repeats, time_limit=time_limit)
    return _SOLVED[key]


def _solver_info(res: cycle.SolveResult, repeats: int) -> dict[str, Any]:
    st = res.stats
    return {
        "layout": "solver",
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
    res = solve_period(pl.spec, gd, 1, p.time_limit)
    reps = 1
    if res.best is not None and lane_repeats(res.best, gd) < pl.periods:
        res = solve_period(pl.spec, gd, pl.periods, p.time_limit)
        reps = pl.periods
    if res.best is None:
        st = res.stats
        why = "timed out" if st.timed_out else "no period within the search bounds"
        raise Infeasible(
            f"solver found no {pl.spec.machines()}-machine period ({why}; {st.summary()})"
        )
    return res.best, _solver_info(res, reps)


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
            for item, s in f.surplus.items():
                if s > _EPS:
                    sup, dem = f.internal[item]
                    note.append(
                        f"{item}: {sup:g}/s made per period, {dem:g}/s used (approximate "
                        f"{pl.spec.stages[1].count}:{pl.spec.stages[0].count} ratio, exact "
                        f"{pl.exact[0]}:{pl.exact[1]}); producers idle the rest"
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


def predict(unit: Unit, gd: GameData) -> dict[str, Any]:
    """Analytic throughput of a chain from its contract and period meta."""
    m = unit.meta
    w, h = unit.layout.size(gd)
    rate = m["period"]["rate"] * m["periods"]
    limits = {"machines": rate}
    for i in unit.interfaces:
        if i.kind != "item" or i.role != "output" or i.entity is None:
            continue
        lanes = 1 if i.lane in ("left", "right") else int(gd.constant("belt_lanes"))
        limits["output_belt"] = tp.belt_lane_capacity(gd, i.entity) * lanes
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
                failures.append(
                    f"{pl.spec.stages[1].count}:{pl.spec.stages[0].count}: " + "; ".join(problems)
                )
                continue
            try:
                per, info = period_for(pl, p, gd)
            except Infeasible as exc:
                tag = f"{pl.spec.stages[1].count}:{pl.spec.stages[0].count} x{pl.periods}"
                failures.append(f"{tag}: {exc.reason}")
                continue
            found = (pl, per, info)
            break
        if found is None:
            changes = _belt_suggestions(p, gd)
            raise Infeasible(" | ".join(failures), [_fmt(c) for c in changes], changes)
        pl, per, info = found
        info["rejected"] = failures
        bad = check_period(per, gd)
        if bad:
            raise Infeasible(f"period breaks its boundary conditions: {bad[:3]}")
        unit = build_unit(per, pl, p, gd)
        a = pl.spec.stages[1].count
        b = pl.spec.stages[0].count
        meta = {
            **unit.meta,
            "family": self.family,
            "params": asdict(p),
            "periods": pl.periods,
            "ratio": [a, b],
            "exact_ratio": list(pl.exact),
            "intermediate": pl.intermediate,
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
