"""CP-SAT backend for one period of a direct-insertion chain (plan-phase4b §1).

Same interface and the same domain as the exhaustive search (:func:`fgen.solve.cycle.solve`):
``CycleSpec -> SolveResult``; belts as full east-flowing rows, machines facing north on
any anchor clear of the belt rows, 1x1 inserters of the spec's tiers on any free tile in
any direction, poles on any free tile; smallest area first, then fewest poles, then
fewest inserters. Only the search is different: constraints are declared, CP-SAT
searches.

Search driver, per height ``H`` (increasing) and grouping of the raw materials into
belts: one model with the width as a variable (``W = sum u[x]``, columns ``u``
non-increasing) and **without poles** screens the whole height (a relaxation: when it is
infeasible, no width of this height works); otherwise fixed-width models are solved for
``W`` upward from the area bound, the first feasible one being the smallest for ``H``.
Later heights only try widths below the best area so far. A fixed-width model with the
width variable is too weak to *prove* the smallest width (its LP bound), and one model
per ``(W, H)`` costs a presolve each; the screen removes most of those.

Variables (all boolean unless stated):

- ``row[b, y]``: belt ``b`` of the skeleton runs on row ``y`` (one row per belt, one
  belt per row; mirror images cut by ``2 * row(belt 0) <= H - 1``);
- ``P[s, a]``: a machine of stage ``s`` has its top-left tile at anchor ``a``;
  ``sum_a P[s, a] = count`` (identical machines carry no identity, so no permutations);
- one variable per inserter slot ``(tile, direction, tier)`` and **purpose**:
  ``IN`` (pick up from input belt ``b``, drop into a machine whose stage needs it),
  ``OUT`` (pick up from a final machine, drop straight onto output belt ``b``),
  ``LINK`` (pick up from a stage-``ps`` machine, drop into a stage-``cs`` machine that
  uses its product);
- ``f[ap, ac]`` (integer, items/s in fixed point): intermediate moved from the producer
  at ``ap`` to the consumer at ``ac``;
- ``Q[q]``: a pole at anchor ``q``.

Constraints: one thing per tile (belt row + machine cover + inserters + poles <= 1);
per machine and needed belt ``sum cap * IN >= rate * P``; per final machine ``sum cap *
OUT >= rate * P``; ``f <= sum cap * LINK`` over the pair's slots, producers ``sum f_out
<= rate * P``, final consumers ``sum f_in >= demand * P``, intermediate consumers
``sum f_in >= ingredient per product * sum f_out`` (multi-level trees, surplus allowed);
every electric machine and every inserter tile supplied by a pole; some pole within wire
reach of the image ``x + W`` of some pole; at least ``W / wire reach`` poles. The poles
being **one** wire network is added lazily: when a solution's poles split into several
components, the cut "some pole on the wire boundary of this component" is added for each
and the model re-solved (valid for every connected solution, so nothing is lost).
Objective: ``poles * 1000 + inserters``. Output-lane loads (an even split of each final
machine's product over its output inserters, as the exhaustive search and
:func:`fgen.solve.period.assemble` count them) are only modelled when they can bind.

OR-Tools is an optional dependency (``uv sync --extra solver``); :func:`available`
says whether it is installed and :func:`solve` raises :class:`SolverUnavailable` with
the install hint otherwise. Every number comes from ``GameData``; the result is
re-checked by :func:`fgen.solve.period.check_period` and ``validate`` like the
exhaustive one.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from typing import Any

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.geom import belt as beltgeom
from fgen.geom import footprint, inserter, pole
from fgen.geom.vec import CARDINALS, Direction, Tile
from fgen.solve import cpsat_fluid
from fgen.solve.cycle import SolveResult, SolveStats
from fgen.solve.period import (
    FLOW,
    CycleFlows,
    CycleSpec,
    Period,
    PlacedInserter,
    SkeletonBelt,
    assemble,
    belt_sets,
    ceil_div,
    center,
    flows,
    input_lanes_ok,
)

SCALE = 10_000  # fixed point for items/s (rates in the data have <= 4 decimals)
POLE_WEIGHT = 1000  # objective: width, then poles, then inserters (fewer than 1000)
WIDTH_WEIGHT = 1000 * POLE_WEIGHT
DEBUG = False
INSTALL_HINT = "the CP-SAT backend needs OR-Tools: `uv sync --extra solver`"


class SolverUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import ortools.sat.python.cp_model  # noqa: F401
    except ImportError:
        return False
    return True


def _cp() -> Any:
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise SolverUnavailable(INSTALL_HINT) from exc
    return cp_model


def _q(v: float) -> int:
    return round(v * SCALE)


class _Ctx:
    """Spec-level numbers shared by every size."""

    def __init__(self, spec: CycleSpec, gd: GameData, repeats: int) -> None:
        self.spec, self.gd, self.repeats = spec, gd, repeats
        self.flows: CycleFlows = flows(spec, gd)
        for n in spec.inserters:
            if footprint.size(gd, n) != (1, 1):
                raise ValueError(f"{n}: the solver handles 1x1 inserters")
        self.caps = {n: tp.inserter_throughput_estimate(gd, n) for n in spec.inserters}
        c0 = Tile(0, 0).center()
        self.reach = [
            (n, d, inserter.pickup_tile(gd, n, c0, d), inserter.drop_tile(gd, n, c0, d))
            for n in spec.inserters
            for d in CARDINALS
        ]
        self.msize = [footprint.size(gd, s.machine) for s in spec.stages]
        self.electric = [gd.entity(s.machine).is_electric for s in spec.stages]
        self.pw, self.ph = footprint.size(gd, spec.pole)
        pc = center(gd, spec.pole, 0, 0)
        self.supply = pole.supply_range(gd, spec.pole, pc)  # offsets from the anchor
        self.pole_c = (pc.x, pc.y)
        self.wire = pole.wire_distance(gd, spec.pole, spec.pole)
        self.lane_cap = tp.belt_lane_capacity(gd, spec.belt)
        self.lanes = int(gd.constant("belt_lanes"))
        f = self.flows
        # fluids (plan-phase5a): pipes instead of belts and inserters
        self.fluid = (
            cpsat_fluid.FluidCtx(spec, gd, f) if f.fluid_inputs or f.fluid_outputs else None
        )
        best = max(self.caps.values())
        self.min_inserters = 0
        for s, sf in zip(spec.stages, f.stages, strict=True):
            lane = set(spec.lane_items)
            per = sum(ceil_div(v, best) for k, v in sf.internal_in.items() if k not in lane)
            per += ceil_div(
                sum(v for k, v in sf.external_in.items() if not gd.is_fluid(k))
                + sum(v for k, v in sf.internal_in.items() if k in lane),
                best,
            )
            per += ceil_div(sf.rate, best) if sf.final and not gd.is_fluid(sf.product) else 0
            self.min_inserters += per * s.count
        self.machine_tiles = sum(
            w * h for (w, h), s in zip(self.msize, spec.stages, strict=True) for _ in [s] * s.count
        )
        out_total = sum(v for k, v in f.output_rate.items() if not gd.is_fluid(k))
        self.output_lanes_bind = out_total * repeats > self.lane_cap + 1e-9


def _poles(
    m: Any,
    ctx: _Ctx,
    h: int,
    w: int,
    w_min: int,
    u: list[Any],
    width: Any,
    occ: dict[tuple[int, int], list[Any]],
    P: list[dict[tuple[int, int], Any]],
    ins_at: dict[tuple[int, int], list[Any]],
    Q: dict[tuple[int, int], Any],
    nbr: dict[tuple[int, int], list[tuple[int, int]]],
) -> Any:
    """Poles (filled into ``Q``, wire neighbours into ``nbr``): supply every machine and
    inserter, reach the next period; returns the pole count (None: cannot reach)."""

    def eq(v: int) -> Any:  # 1 iff W == v
        return (u[v - 1] if v >= 1 else 1) - (u[v] if v < w else 0)

    stages = range(len(ctx.spec.stages))
    sx0, sy0, sx1, sy1 = ctx.supply
    sup_rect: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for y in range(h - ctx.ph + 1):
        for x in range(w - ctx.pw + 1):
            v = m.new_bool_var(f"Q{x}_{y}")
            m.add(v <= u[x + ctx.pw - 1])
            Q[(x, y)] = v
            for i in range(ctx.pw):
                for j in range(ctx.ph):
                    occ[(x + i, y + j)].append(v)
            sup_rect[(x, y)] = (x + sx0, y + sy0, x + sx1, y + sy1)
    for si in stages:
        if not ctx.electric[si]:
            continue
        mw, mh = ctx.msize[si]
        for (ax, ay), pv in P[si].items():
            sup = [
                Q[q]
                for q, (x0, y0, x1, y1) in sup_rect.items()
                if x0 <= ax + mw - 1 and x1 >= ax and y0 <= ay + mh - 1 and y1 >= ay
            ]
            m.add(sum(sup) >= pv)
    for (x, y), vs in ins_at.items():
        sup = [Q[q] for q, (x0, y0, x1, y1) in sup_rect.items() if x0 <= x <= x1 and y0 <= y <= y1]
        m.add(sum(sup) >= sum(vs))
    qs = list(Q)
    cen = {q: (q[0] + ctx.pole_c[0], q[1] + ctx.pole_c[1]) for q in qs}
    d2 = ctx.wire**2 + 1e-6
    n_poles = sum(Q.values())
    # copies need a pole per wire reach: poles * reach >= W (reach as a fraction)
    reach = Fraction(ctx.wire).limit_denominator(1000)
    m.add(n_poles * reach.numerator >= width * reach.denominator)
    m.add(n_poles >= 1)
    for a in qs:
        nbr[a] = [
            b
            for b in qs
            if b != a and (cen[a][0] - cen[b][0]) ** 2 + (cen[a][1] - cen[b][1]) ** 2 <= d2
        ]
    # some pole reaches the image (x + W) of some pole: the pole k columns left of the
    # right edge (W - 1 - k) against a pole near the left edge
    kmax = math.ceil(ctx.wire)
    edge: dict[tuple[int, int], list[Any]] = defaultdict(list)  # (k, y) -> z vars
    for (x, y), qv in Q.items():
        for k in range(kmax + 1):
            v = x + 1 + k
            if v < w_min or v > w:
                continue
            z = m.new_bool_var("")
            m.add(z <= qv)
            m.add(z <= eq(v))
            edge[(k, y)].append(z)
    cross = []
    for (k, ya), zs in edge.items():
        for (xb, yb), qv in Q.items():
            dx = (xb + 1 + k) + ctx.pole_c[0] - ctx.pole_c[0]
            if dx**2 + (yb - ya) ** 2 <= d2:
                c = m.new_bool_var("")
                m.add(c <= sum(zs))
                m.add(c <= qv)
                cross.append(c)
    if not cross:
        return None
    m.add(sum(cross) >= 1)
    return n_poles


def _solve_height(
    ctx: _Ctx,
    h: int,
    w_min: int,
    w: int,
    belts: list[SkeletonBelt],
    seconds: float,
    workers: int,
    stats: SolveStats,
    optimize: bool = True,
    power: bool = True,
) -> tuple[str, Period | None, float]:
    """One CP-SAT model for height ``h`` and widths ``w_min..w`` (the width is a
    variable); ``optimize``: minimise width, poles, inserters, else stop at the first
    feasible period; without ``power`` the poles are left out (a relaxation: only its
    infeasibility means anything). Returns (status name, period, objective)."""
    cp_model = _cp()
    spec, f = ctx.spec, ctx.flows
    m = cp_model.CpModel()
    tiles = [(x, y) for y in range(h) for x in range(w)]
    occ: dict[tuple[int, int], list[Any]] = defaultdict(list)
    # width: column x is inside the period iff u[x]; W = sum(u), u non-increasing
    u = [m.new_bool_var(f"u{x}") for x in range(w)]
    for x in range(w - 1):
        m.add(u[x] >= u[x + 1])
    for x in range(w_min):
        m.add(u[x] == 1)
    width = sum(u)

    def eq(v: int) -> Any:  # 1 iff W == v
        return (u[v - 1] if v >= 1 else 1) - (u[v] if v < w else 0)

    # belts
    nb = len(belts)
    row = [[m.new_bool_var(f"row{b}_{y}") for y in range(h)] for b in range(nb)]
    for b in range(nb):
        m.add_exactly_one(row[b])
    for y in range(h):
        m.add_at_most_one(row[b][y] for b in range(nb))
        for x in range(w):
            occ[(x, y)] += [row[b][y] for b in range(nb)]
    m.add(2 * sum(y * row[0][y] for y in range(h)) <= h - 1)
    in_belts = {k: b for k, b in enumerate(belts) if b.role == "input"}
    out_belts = {k: b for k, b in enumerate(belts) if b.role == "output"}

    # machines
    stages = range(len(spec.stages))
    P: list[dict[tuple[int, int], Any]] = []
    cover: list[dict[tuple[int, int], list[tuple[tuple[int, int], Any]]]] = []
    for si in stages:
        mw, mh = ctx.msize[si]
        ps: dict[tuple[int, int], Any] = {}
        cv: dict[tuple[int, int], list[tuple[tuple[int, int], Any]]] = defaultdict(list)
        for y in range(h - mh + 1):
            for x in range(w - mw + 1):
                v = m.new_bool_var(f"P{si}_{x}_{y}")
                m.add(v <= u[x + mw - 1])
                ps[(x, y)] = v
                for i in range(mw):
                    for j in range(mh):
                        cv[(x + i, y + j)].append(((x, y), v))
                        occ[(x + i, y + j)].append(v)
        m.add(sum(ps.values()) == spec.stages[si].count)
        P.append(ps)
        cover.append(cv)

    def covered(sis: list[int], t: tuple[int, int]) -> list[Any]:
        return [v for si in sis for _, v in cover[si].get(t, [])]

    # which belt each stage needs, at what rate
    belt_of = {(b.role, mat): k for k, b in enumerate(belts) for mat in b.materials}
    need_in: list[dict[int, float]] = []
    lane_set = set(spec.lane_items)
    for sf in f.stages:
        d: dict[int, float] = defaultdict(float)
        for mat, r in [*sf.external_in.items(), *sf.internal_in.items()]:
            if (mat in sf.external_in and not ctx.gd.is_fluid(mat)) or mat in lane_set:
                d[belt_of[("input", mat)]] += r
        need_in.append(dict(d))
    # belts that carry a lane intermediate: which item, which stages make it, its side
    # lane intermediates: (belt, item) keys; per belt one side literal: its first lane
    # item rides the left lane iff it is 1, a second one the other lane
    lane_keys = [(k, mat) for k, b in in_belts.items() for mat in b.materials if mat in lane_set]
    lane_belts = {k for k, _ in lane_keys}
    lane_makers = {
        key: [si for si in stages if f.stages[si].product == key[1]] for key in lane_keys
    }
    side = {k: m.new_bool_var(f"left{k}") for k in lane_belts}

    def first_lane_item(key: tuple[int, str]) -> bool:
        return next(mat for mat in belts[key[0]].materials if mat in lane_set) == key[1]

    # input ports stay reachable: a raw material that has only one lane of its belt is
    # side-loaded at the west edge from that lane's side, so the neighbouring row on that
    # side must not be another belt (two such feeders facing each other would cross)
    def lane_dy(lane: str) -> int:
        return round(beltgeom.lane_side(FLOW, lane).vector().y)  # type: ignore[arg-type]

    side_rows: list[tuple[Any, int, Any]] = []  # (belt on y, its feeder's side row, slack)
    for k, b in in_belts.items():
        raws = [mat for mat in b.materials if mat not in lane_set]
        if len(b.materials) < 2 or not raws:
            continue
        for lane in ("left", "right"):
            if len(raws) == 2:
                slack: Any = 0
            else:  # one lane item: the raw rides the other lane
                item_left = side[k]
                slack = item_left if lane == "left" else 1 - item_left
            for y in range(h):
                yy = y + lane_dy(lane)
                if 0 <= yy < h:
                    m.add(row[k][y] + sum(row[o][yy] for o in range(nb)) <= 1 + slack)
                    side_rows.append((row[k][y], yy, slack))

    users = {k: [si for si in stages if k in need_in[si]] for k in in_belts}
    makers = {
        k: [si for si in stages if f.stages[si].final and f.stages[si].product == b.materials[0]]
        for k, b in out_belts.items()
    }
    pairs = [
        (ps, cs)
        for ps in stages
        for cs in stages
        if ps != cs
        and f.stages[ps].product in f.stages[cs].internal_in
        and f.stages[ps].product not in lane_set
    ]

    # inserter slots by purpose
    ins_at: dict[tuple[int, int], list[Any]] = defaultdict(list)
    in_drop: dict[tuple[int, tuple[int, int]], list[tuple[Any, int]]] = defaultdict(list)
    out_pick: dict[tuple[int, int], list[tuple[Any, int, int, str]]] = defaultdict(list)
    link_vars: list[tuple[Any, int, int, tuple[int, int], tuple[int, int], PlacedInserter]] = []
    lane_drop: dict[tuple[int, int], list[tuple[Any, int, Any]]] = defaultdict(list)
    lane_pick: dict[int, list[tuple[Any, int]]] = defaultdict(list)  # belt -> (IN, pickup x)
    lane_put: dict[Any, list[tuple[Any, int]]] = defaultdict(list)  # key -> (LANE, drop x)
    slots: list[tuple[Any, PlacedInserter, tuple[int, int], tuple[int, int]]] = []
    for x, y in tiles:
        for tier, d, pu, dr in ctx.reach:
            px, py, dx, dy = x + pu.x, y + pu.y, x + dr.x, y + dr.y
            if not (0 <= px < w and 0 <= py < h and 0 <= dx < w and 0 <= dy < h):
                continue
            cap = _q(ctx.caps[tier])
            inside = u[max(x, px, dx)]
            for k in in_belts:
                if py == y:
                    continue
                tgt = covered(users[k], (dx, dy))
                if not tgt:
                    continue
                v = m.new_bool_var("")
                m.add(v <= inside)
                m.add(v <= row[k][py])
                m.add(v <= sum(tgt))
                ins_at[(x, y)].append(v)
                in_drop[(k, (dx, dy))].append((v, cap))
                if k in lane_belts:
                    lane_pick[k].append((v, px))
                slots.append((v, PlacedInserter(x, y, d, tier, "in", -1, k), (px, py), (dx, dy)))
            for k in out_belts:
                if dx != x or dy == y:
                    continue
                src = covered(makers[k], (px, py))
                if not src:
                    continue
                v = m.new_bool_var("")
                m.add(v <= inside)
                m.add(v <= row[k][dy])
                m.add(v <= sum(src))
                approach = Direction.S if dy > y else Direction.N
                lane = beltgeom.drop_lane(FLOW, approach)
                ins_at[(x, y)].append(v)
                out_pick[(px, py)].append((v, cap, k, lane))
                slots.append(
                    (v, PlacedInserter(x, y, d, tier, "out", -1, k, lane), (px, py), (dx, dy))
                )
            for key in lane_keys:
                k = key[0]
                if dx != x or dy == y:
                    continue
                src = covered(lane_makers[key], (px, py))
                if not src:
                    continue
                v = m.new_bool_var("")
                m.add(v <= inside)
                m.add(v <= row[k][dy])
                m.add(v <= sum(src))
                ln = beltgeom.drop_lane(FLOW, Direction.S if dy > y else Direction.N)
                on_left = (ln == "left") == first_lane_item(key)
                m.add(v <= (side[k] if on_left else 1 - side[k]))
                ins_at[(x, y)].append(v)
                lane_drop[(px, py)].append((v, cap, key))
                slots.append(
                    (v, PlacedInserter(x, y, d, tier, "lane", -1, k, ln), (px, py), (dx, dy))
                )
                lane_put[key].append((v, x))
            for ps, cs in pairs:
                src, tgt = covered([ps], (px, py)), covered([cs], (dx, dy))
                if not src or not tgt:
                    continue
                v = m.new_bool_var("")
                m.add(v <= inside)
                m.add(v <= sum(src))
                m.add(v <= sum(tgt))
                ins_at[(x, y)].append(v)
                pi = PlacedInserter(x, y, d, tier, "link", -1, -1)
                link_vars.append((v, ps, cs, (px, py), (dx, dy), pi))
                slots.append((v, pi, (px, py), (dx, dy)))
    for t, vs in ins_at.items():
        occ[t] += vs

    # rates: belt -> machine, machine -> output belt
    def fp(si: int, a: tuple[int, int]) -> list[tuple[int, int]]:
        mw, mh = ctx.msize[si]
        return [(a[0] + i, a[1] + j) for i in range(mw) for j in range(mh)]

    out_flow: dict[tuple[int, int], list[tuple[Any, Any, int, str]]] = {}
    for si in stages:
        sf = f.stages[si]
        for a, pv in P[si].items():
            ts = fp(si, a)
            for k, r in need_in[si].items():
                terms = [(v, c) for t in ts for v, c in in_drop.get((k, t), [])]
                if not terms:
                    m.add(pv == 0)
                    continue
                m.add(sum(c * v for v, c in terms) >= _q(r) * pv)
            if sf.final and not ctx.gd.is_fluid(sf.product):
                terms2 = [e for t in ts for e in out_pick.get(t, [])]
                if not terms2:
                    m.add(pv == 0)
                    continue
                m.add(sum(c * v for v, c, _, _ in terms2) >= _q(sf.rate) * pv)
                if ctx.output_lanes_bind:
                    out_flow[(si, a)] = [(pv, v, k, ln) for v, _, k, ln in terms2]

    # intermediates: flow per producer/consumer anchor pair
    pair_terms: dict[tuple[int, int, tuple[int, int], tuple[int, int]], list[Any]] = defaultdict(
        list
    )
    for v, ps, cs, pt, dt, pi in link_vars:
        cap = _q(ctx.caps[pi.tier])
        for ap, _ in cover[ps][pt]:
            for ac, _ in cover[cs][dt]:
                pair_terms[(ps, cs, ap, ac)].append((v, cap))
    give: dict[tuple[int, tuple[int, int]], list[Any]] = defaultdict(list)
    take: dict[tuple[int, str, tuple[int, int]], list[Any]] = defaultdict(list)
    for (ps, cs, ap, ac), terms in pair_terms.items():
        if set(fp(ps, ap)) & set(fp(cs, ac)):
            continue
        item = f.stages[ps].product
        hi = _q(min(f.stages[ps].rate, f.stages[cs].internal_in[item]))
        fv = m.new_int_var(0, hi, "")
        m.add(fv <= sum(c * v for v, c in terms))
        m.add(fv <= hi * P[ps][ap])
        m.add(fv <= hi * P[cs][ac])
        give[(ps, ap)].append(fv)
        take[(cs, item, ac)].append(fv)
    # lane intermediates: what each maker drops onto its lane covers the period's use,
    # and every drop lies upstream (west) of every pickup from that belt
    for key in lane_keys:
        k, item = key
        made = []
        for ps in lane_makers[key]:
            for ap, pv in P[ps].items():
                terms = [
                    (v, c) for t in fp(ps, ap) for v, c, kk in lane_drop.get(t, []) if kk == key
                ]
                g = m.new_int_var(0, _q(f.stages[ps].rate), "")
                m.add(g <= sum(c * v for v, c in terms))
                m.add(g <= _q(f.stages[ps].rate) * pv)
                give[(ps, ap)].append(g)
                made.append(g)
        m.add(sum(made) >= _q(f.internal[item][1]))
        m.add(sum(made) <= _q(ctx.lane_cap))
        last = m.new_int_var(0, w, "")
        for v, x in lane_put.get(key, []):
            m.add(last >= x * v)
        for v, x in lane_pick.get(k, []):
            m.add(x >= last - w * (1 - v))
    for ps in stages:
        if f.stages[ps].final:
            continue
        for ap, pv in P[ps].items():
            if give.get((ps, ap)):
                m.add(sum(give[(ps, ap)]) <= _q(f.stages[ps].rate) * pv)
    # consumers: a final machine runs at full speed; an intermediate one needs its
    # ingredients in proportion to what it hands on (multi-level trees with surplus)
    for cs in stages:
        csf = f.stages[cs]
        for item, dem in csf.internal_in.items():
            if item in lane_set:
                continue  # taken from the belt with the raw inputs (need_in)
            for ac, cv in P[cs].items():
                got = take.get((cs, item, ac), [])
                if csf.final:
                    m.add(sum(got) >= _q(dem) * cv)
                else:
                    m.add(_q(csf.rate) * sum(got) >= _q(dem) * sum(give.get((cs, ac), [])))

    # output lanes, only when they can bind: even split over a machine's output inserters
    if out_flow:
        lane_load: dict[tuple[int, str], list[Any]] = defaultdict(list)
        for (si, _a), terms in out_flow.items():
            rate = _q(f.stages[si].rate)
            n_all = m.new_int_var(0, len(terms), "")
            m.add(n_all == sum(v for _, v, _, _ in terms))
            for key in {(k, ln) for _, _, k, ln in terms}:
                n_l = sum(v for _, v, k, ln in terms if (k, ln) == key)
                share = m.new_int_var(0, rate, "")
                prod = m.new_int_var(0, rate * len(terms), "")
                m.add_multiplication_equality(prod, [share, n_all])
                m.add(prod >= rate * n_l)
                lane_load[key].append(share)
        cap_l = math.floor(_q(ctx.lane_cap) / ctx.repeats)
        for shares in lane_load.values():
            m.add(sum(shares) <= cap_l)

    Q: dict[tuple[int, int], Any] = {}
    nbr: dict[tuple[int, int], list[tuple[int, int]]] = {}
    if power:
        n_poles = _poles(m, ctx, h, w, w_min, u, width, occ, P, ins_at, Q, nbr)
        if n_poles is None:
            return "INFEASIBLE", None, math.inf
    else:
        n_poles = 0
    fv = None
    if ctx.fluid is not None:
        fv = cpsat_fluid.add(m, ctx.fluid, h, w, u, w_min == w, occ, P)
        # nor may a fluid's crossing (its port at the west end) take that side row
        for bv, yy, slack in side_rows:
            for crossing in fv.cross.values():
                m.add(bv + crossing[yy] <= 1 + slack)
    for t in tiles:
        m.add(sum(occ[t]) <= 1)
    n_ins = sum(v for v, *_ in slots) + (fv.count if fv is not None else 0)
    if optimize:
        m.minimize(WIDTH_WEIGHT * width + POLE_WEIGHT * n_poles + n_ins)

    stats.variables["cp_vars_max"] = max(
        stats.variables.get("cp_vars_max", 0), len(m.proto.variables)
    )
    stats.variables["cp_constraints_max"] = max(
        stats.variables.get("cp_constraints_max", 0), len(m.proto.constraints)
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.01, seconds)
    solver.parameters.num_workers = workers
    # deterministic parallel search: the same spec always gives the same period (the
    # blueprint sha keeps measurements across regenerations)
    solver.parameters.interleave_search = True
    if not optimize:
        solver.parameters.stop_after_first_solution = True
    # the poles must be one wire network: solved without it, then for every pole set
    # that splits, a cut "some pole on the boundary of this component" is added
    t_solve = time.monotonic()
    t_end = t_solve + seconds
    while True:
        solver.parameters.max_time_in_seconds = max(0.01, t_end - time.monotonic())
        status = solver.solve(m)
        stats.nodes["cp_solves"] += 1
        stats.nodes["cp_branches"] += solver.num_branches
        stats.nodes["cp_conflicts"] += solver.num_conflicts
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        placed = [q for q in Q if solver.value(Q[q])]
        comps = _components(placed, nbr)
        if len(comps) <= 1:
            break
        for comp in comps:
            border = {b for a in comp for b in nbr[a]} - comp
            other = next(q for q in placed if q not in comp)
            a0 = next(iter(comp))
            m.add(sum(Q[b] for b in border) >= Q[a0] + Q[other] - 1)
            stats.pruned["cp_wire_cuts"] += 1
    name = solver.status_name(status)
    stats.nodes["cp_models"] += 1
    if DEBUG:
        print(
            f"  h={h} w<={w} {[b.materials for b in belts]} {name} "
            f"{time.monotonic() - t_solve:.2f}s branches {solver.num_branches}"
        )
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return name, None, math.inf

    # read the solution back
    rows = [next(y for y in range(h) if solver.value(row[b][y])) for b in range(nb)]
    machines: list[tuple[int, int, int]] = []
    owner: dict[tuple[int, int], int] = {}
    anchors: dict[tuple[int, tuple[int, int]], int] = {}
    for si in stages:
        for a, pv in sorted(P[si].items(), key=lambda kv: (kv[0][1], kv[0][0])):
            if solver.value(pv):
                for t in fp(si, a):
                    owner[t] = len(machines)
                anchors[(si, a)] = len(machines)
                machines.append((si, a[0], a[1]))
    chosen: list[PlacedInserter] = []
    for v, pi, pt, dt in slots:
        if not solver.value(v):
            continue
        if pi.kind == "in":
            chosen.append(replace(pi, machine=owner[dt]))
        elif pi.kind in ("out", "lane"):
            chosen.append(replace(pi, machine=owner[pt]))
        else:
            chosen.append(
                PlacedInserter(**{**pi.__dict__, "machine": owner[dt], "other": owner[pt]})
            )
    poles = [q for q, v in Q.items() if solver.value(v)]
    w_sol = sum(solver.value(v) for v in u)
    sides: dict[int, dict[str, str]] = {}
    for k, item in lane_keys:
        left = bool(solver.value(side[k])) == first_lane_item((k, item))
        sides.setdefault(k, {})[item] = "left" if left else "right"
    pipes, crossings, dirs = None, None, None
    if fv is not None and ctx.fluid is not None:
        pipes, crossings = cpsat_fluid.read(solver, ctx.fluid, fv)
        dirs = cpsat_fluid.directions(solver, ctx.fluid, fv, anchors)
    per = assemble(
        spec,
        ctx.gd,
        w_sol,
        h,
        belts,
        rows,
        machines,
        chosen,
        poles,
        f,
        sides,
        dirs,
        pipes,
        crossings,
    )
    per.meta["proved_optimal"] = status == cp_model.OPTIMAL or not optimize
    return name, per, solver.objective_value if optimize else math.inf


def _components(
    placed: list[tuple[int, int]], nbr: dict[tuple[int, int], list[tuple[int, int]]]
) -> list[set[tuple[int, int]]]:
    left, out = set(placed), []
    while left:
        seen = {left.pop()}
        stack = list(seen)
        while stack:
            for b in nbr[stack.pop()]:
                if b in left:
                    left.discard(b)
                    seen.add(b)
                    stack.append(b)
        out.append(seen)
    return out


def solve(
    spec: CycleSpec,
    gd: GameData,
    *,
    repeats: int = 1,
    max_width: int = 32,
    max_height: int = 16,
    time_limit: float = 60.0,
    candidates: int = 3,
    workers: int = 4,
    screen_seconds: float = 30.0,
    **_exhaustive_only: Any,
) -> SolveResult:
    """Smallest periods for ``spec``: same contract as :func:`fgen.solve.cycle.solve`
    (``candidates`` of the smallest area, fewest poles then inserters first)."""
    _cp()
    t0 = time.monotonic()
    deadline = t0 + time_limit
    stats = SolveStats()
    ctx = _Ctx(spec, gd, repeats)
    stats.variables.update(machines=spec.machines(), min_inserters=ctx.min_inserters)
    mw = max(w for w, _ in ctx.msize)
    mh = max(h for _, h in ctx.msize)
    all_sets = belt_sets(ctx.flows, ctx.lanes, spec.lane_items)
    skeletons = [
        bs for bs in all_sets if input_lanes_ok(bs, ctx.flows, ctx.lane_cap, ctx.lanes, repeats)
    ]
    stats.pruned["lane_capacity"] += len(all_sets) - len(skeletons)
    found: list[tuple[float, Period]] = []
    best_area: int | None = None
    for h in range(mh + 1, max_height + 1):
        ts = time.monotonic()
        n_found = 0
        w_hi = max_width if best_area is None else min(max_width, best_area // h)
        for belts in skeletons:
            # area bound: belt rows + machine tiles + fewest inserters + one pole
            fixed = ctx.machine_tiles + ctx.min_inserters + 1
            w_lo = max(mw, math.ceil(fixed / (h - len(belts))) if h > len(belts) else w_hi + 1)
            if w_lo > w_hi:
                stats.pruned["area_bound"] += 1
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                stats.timed_out = True
                break
            # screen every width of this height at once, without the poles (a relaxation:
            # infeasible there = infeasible), then the fixed widths smallest first
            status, _, _ = _solve_height(
                ctx, h, w_lo, w_hi, belts, min(left, screen_seconds), workers, stats, False, False
            )
            stats.pruned[f"cp_screen_{status.lower()}"] += 1
            if status == "INFEASIBLE":
                continue
            per2 = None
            for wf in range(w_lo, w_hi + 1):
                left = deadline - time.monotonic()
                status, per2, obj = _solve_height(
                    ctx, h, wf, wf, belts, left, workers, stats, optimize=True
                )
                stats.pruned[f"cp_{status.lower()}"] += 1
                if status == "UNKNOWN" or (per2 is not None and not per2.meta["proved_optimal"]):
                    stats.timed_out = True
                if per2 is not None or stats.timed_out:
                    break
            per = per2
            if per is not None:
                per.meta["seconds_to_find"] = round(time.monotonic() - t0, 3)
                found.append((obj, per))
                n_found += 1
                best_area = per.area if best_area is None else min(best_area, per.area)
                w_hi = min(w_hi, best_area // h)
            if stats.timed_out:
                break
        stats.sizes.append(
            {
                "width": w_hi,
                "height": h,
                "seconds": round(time.monotonic() - ts, 3),
                "nodes": 0,
                "found": n_found,
                "budget_cuts": 0,
            }
        )
        if stats.timed_out:
            break
    stats.seconds = time.monotonic() - t0
    found.sort(key=lambda op: (op[1].area, op[1].meta["poles"], op[1].meta["inserters"]))
    return SolveResult([p for _, p in found[:candidates]], stats)
