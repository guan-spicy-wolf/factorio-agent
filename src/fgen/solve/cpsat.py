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

**Palette and presets (plan-phase4c).** The spec's inserter, pole and belt tiers are a
palette: every inserter slot and pole anchor carries its tier, every belt row a tier
variable ``bt[b, t]`` (lane capacities become constraints on it), and a belt row may
hold underground pairs of its tier (``ugi``/``ugo``/``gap`` per column; inserters pick
up and drop only on plain belt tiles). With one tier of each and no undergrounds the
model is exactly the phase-4b one. The objective is a preset of
:mod:`fgen.solve.preset` (one integer key; ``compact`` ends in the palette cost, so a
tie in area, poles and inserters takes the cheapest tiers; ``compact`` with
``prove=True`` keeps the phase-4b objective and driver, whose models do not break that
tie, and only ranks its candidates by the key).

**Drivers.** ``prove=True`` (characterization): the phase-4b driver above for
``compact``; for the other presets every (height, skeleton, width) is solved to
optimality. Default (**anytime**, plan-phase4c), within a time box:

0. seeds: if every stage count is ``g`` times a smaller spec's, that spec's period
   tiled ``g`` times (:func:`repeat_period`); else, with a palette, the primary tiers
   alone (stage 0, given up when its loose sizes find nothing);
1. first feasible period: analytically loose sizes (:func:`loose_sizes`), then up the
   heights the pole-free screen and the widest model of the first height it lets
   through (fluids skip the loose sizes: wide fluid models are expensive);
2. one pass over (height, skeleton), the incumbent's first: the screen; ``compact``:
   the smallest width that beats the incumbent by bisection over fixed-width models
   (first solutions with ``key <= best - 1``), then the unrefuted widths below it
   upward to close the proof; fluids or no incumbent: widths upward; ``cheap``/
   ``simple``: bisection, never closed (wider may rank better). Each model runs to its
   end under a cap (a share of what is left: restarting a model pays its presolve
   again);
3. the preset's key at the final best size with the reserve (no bound row: it slowed
   the same model down 5x).

The box is a deterministic budget (CP-SAT deterministic time plus a per-model charge
for building and presolve, proportional to its variables), so the same spec gives the
same period; the wall clock only caps runaways. ``proven``: every (height, skeleton)
closed (``INFEASIBLE``/``OPTIMAL``); none found and not proven: ``unproven_infeasible``.

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
from fgen.solve import preset as presets
from fgen.solve.cycle import SolveResult, SolveStats, finish_stats
from fgen.solve.period import (
    FLOW,
    CycleFlows,
    CycleSpec,
    Period,
    PlacedInserter,
    SkeletonBelt,
    assemble,
    belt_rate,
    belt_sets,
    ceil_div,
    center,
    check_period,
    flows,
    input_lanes_ok,
    tile,
    underground_of,
)

SCALE = 10_000  # fixed point for items/s (rates in the data have <= 4 decimals)
POLE_WEIGHT = 1000  # objective: width, then poles, then inserters (fewer than 1000)
WIDTH_WEIGHT = 1000 * POLE_WEIGHT
DEBUG = False
PARAMS: dict[str, Any] = {}  # extra CP-SAT parameters (experiments only)
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
        self.pole_tiers = [_PoleTier(gd, n) for n in spec.poles]
        self.wire = max(pt.wire for pt in self.pole_tiers)
        self.belt_tiers = list(spec.belts)
        self.lane_caps = [tp.belt_lane_capacity(gd, b) for b in self.belt_tiers]
        self.lane_cap = max(self.lane_caps)
        self.ug = [underground_of(gd, b, spec.undergrounds) for b in self.belt_tiers]
        self.ug_reach = [int(gd.entity(n).max_distance or 0) if n else 0 for n in self.ug]
        self.lanes = int(gd.constant("belt_lanes"))
        self.scale: presets.Scale | None = None  # set by solve()
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
        self.out_total = out_total
        self.output_lanes_bind = out_total * repeats > min(self.lane_caps) + 1e-9
        self._rows: dict[Any, Any] = {}  # belt_rows cache

    def palette_names(self) -> list[str]:
        """Every non-machine entity the model may place (costs, kinds)."""
        names = [*self.spec.inserters, *self.spec.poles, *self.belt_tiers]
        names += [n for n in self.ug if n]
        if self.fluid is not None:
            names.append(self.spec.pipe)
            if self.spec.pipe_to_ground:
                names.append(self.spec.pipe_to_ground)
        return list(dict.fromkeys(names))

    def belt_rows(
        self, h: int, belts: list[SkeletonBelt]
    ) -> tuple[set[tuple[int, int]], list[set[int]]] | None:
        """Rows the belts and machine anchors can take at height ``h`` (plan-phase8 C):
        a relaxation of the model that ignores x, solved by enumerating the belts' rows.
        Under one assignment a stage's anchor row is possible when its footprint rows hold
        no belt and every belt each of its machines must use is in reach of a north/south
        inserter standing on a row with no belt and outside the machine: every input belt
        it takes from (``need_in``, at full speed) and, for a final stage, its output belt.
        Returns the (belt, row) pairs and per stage the anchor rows of some passing
        assignment, or None when none passes (the model is infeasible). With underground
        belts a belt row has free tiles: nothing is pruned. Cached per (height, skeleton)."""
        key = (h, tuple(belts))
        if key not in self._rows:
            self._rows[key] = self._belt_rows(h, belts)
        return self._rows[key]

    def _belt_rows(
        self, h: int, belts: list[SkeletonBelt]
    ) -> tuple[set[tuple[int, int]], list[set[int]]] | None:
        from itertools import permutations

        f, nb = self.flows, len(belts)
        if any(self.ug):
            return {(k, r) for k in range(nb) for r in range(h)}, [set(range(h)) for _ in f.stages]
        lane = set(self.spec.lane_items)
        belt_of = {(b.role, mat): k for k, b in enumerate(belts) for mat in b.materials}
        need: list[set[int]] = []
        out: list[set[int]] = []
        for sf in f.stages:
            need.append(
                {
                    belt_of[("input", mat)]
                    for mat in [*sf.external_in, *sf.internal_in]
                    if (mat in sf.external_in and not self.gd.is_fluid(mat)) or mat in lane
                }
            )
            fin = sf.final and not self.gd.is_fluid(sf.product)
            out.append({belt_of[("output", sf.product)]} if fin else set())
        # per stage, anchor row y and belt row r: the rows (bit mask) of an inserter that
        # takes from row r into the machine, or drops onto row r out of it
        take_m: list[dict[tuple[int, int], int]] = []
        put_m: list[dict[tuple[int, int], int]] = []
        for _, mh in self.msize:
            tk: dict[tuple[int, int], int] = defaultdict(int)
            pt: dict[tuple[int, int], int] = defaultdict(int)
            for y in range(h - mh + 1):
                for _, _, pu, dr in self.reach:
                    if pu.x or dr.x:
                        continue
                    for yi in range(h):
                        py, dy = yi + pu.y, yi + dr.y
                        if y <= yi < y + mh or not (0 <= py < h and 0 <= dy < h):
                            continue
                        if y <= dy < y + mh:
                            tk[(y, py)] |= 1 << yi
                        if y <= py < y + mh:
                            pt[(y, dy)] |= 1 << yi
            take_m.append(tk)
            put_m.append(pt)
        full = (1 << h) - 1
        ok_rows: set[tuple[int, int]] = set()
        anchors: list[set[int]] = [set() for _ in f.stages]
        for rows in permutations(range(h), nb):
            if 2 * rows[0] > h - 1:
                continue  # the model's mirror cut
            bm = 0
            for r in rows:
                bm |= 1 << r
            free = full & ~bm
            ys: list[list[int]] = []
            for si, (_, mh) in enumerate(self.msize):
                tk, pt = take_m[si], put_m[si]
                good = [
                    y
                    for y in range(h - mh + 1)
                    if not (((1 << mh) - 1) << y) & bm
                    and all(tk.get((y, rows[k]), 0) & free for k in need[si])
                    and all(pt.get((y, rows[k]), 0) & free for k in out[si])
                ]
                if not good:
                    break
                ys.append(good)
            else:
                ok_rows.update(enumerate(rows))
                for si, g in enumerate(ys):
                    anchors[si].update(g)
        return (ok_rows, anchors) if ok_rows else None

    def slots_ok(self) -> bool:
        """Necessary condition (seconds, no model): every machine has at least as many
        tiles an inserter can serve it from as the fewest inserters its rates need."""
        best = max(self.caps.values())
        lane = set(self.spec.lane_items)
        for (mw, mh), sf in zip(self.msize, self.flows.stages, strict=True):
            fp = {(x, y) for x in range(mw) for y in range(mh)}
            tiles = {
                (x, y)
                for x in range(-3 - mw, mw + 4)
                for y in range(-3 - mh, mh + 4)
                if (x, y) not in fp
                for _, _, pu, dr in self.reach
                if (x + pu.x, y + pu.y) in fp or (x + dr.x, y + dr.y) in fp
            }
            need = sum(ceil_div(v, best) for k, v in sf.internal_in.items() if k not in lane)
            need += sum(
                ceil_div(v, best) for k, v in sf.external_in.items() if not self.gd.is_fluid(k)
            )
            if sf.final and not self.gd.is_fluid(sf.product):
                need += ceil_div(sf.rate, best)
            if need > len(tiles):
                return False
        return True


class _PoleTier:
    """Numbers of one pole tier: footprint, supply offsets from the anchor, centre."""

    def __init__(self, gd: GameData, name: str) -> None:
        self.name = name
        self.pw, self.ph = footprint.size(gd, name)
        pc = center(gd, name, 0, 0)
        self.supply = pole.supply_range(gd, name, pc)
        self.c = (pc.x, pc.y)
        self.wire = pole.wire_distance(gd, name, name)
        self.reach = Fraction(self.wire).limit_denominator(1000)


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
    Q: dict[tuple[int, int, int], Any],
    nbr: dict[tuple[int, int, int], list[tuple[int, int, int]]],
) -> Any:
    """Poles (filled into ``Q``, keyed ``(tier, x, y)``; wire neighbours into ``nbr``):
    supply every machine and inserter, reach the next period; returns the pole count
    (None: cannot reach). One tier: exactly the phase-4b constraints."""

    def eq(v: int) -> Any:  # 1 iff W == v
        return (u[v - 1] if v >= 1 else 1) - (u[v] if v < w else 0)

    tiers = ctx.pole_tiers
    stages = range(len(ctx.spec.stages))
    sup_rect: dict[tuple[int, int, int], tuple[int, int, int, int]] = {}
    for ti, pt in enumerate(tiers):
        sx0, sy0, sx1, sy1 = pt.supply
        for y in range(h - pt.ph + 1):
            for x in range(w - pt.pw + 1):
                v = m.new_bool_var(f"Q{x}_{y}" if ti == 0 else f"Q{ti}_{x}_{y}")
                m.add(v <= u[x + pt.pw - 1])
                Q[(ti, x, y)] = v
                for i in range(pt.pw):
                    for j in range(pt.ph):
                        occ[(x + i, y + j)].append(v)
                sup_rect[(ti, x, y)] = (x + sx0, y + sy0, x + sx1, y + sy1)
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
    cen = {q: (q[1] + tiers[q[0]].c[0], q[2] + tiers[q[0]].c[1]) for q in qs}

    d2s = {
        (a, b): pole.wire_distance(ctx.gd, tiers[a].name, tiers[b].name) ** 2 + 1e-6
        for a in range(len(tiers))
        for b in range(len(tiers))
    }

    def d2(a: int, b: int) -> float:
        return d2s[(a, b)]

    n_poles = sum(Q.values())
    # copies need a pole per wire reach: sum over poles of their reach >= W (each wire
    # of the chain from a pole to its image is at most its start pole's reach)
    if len(tiers) == 1:
        reach = tiers[0].reach
        m.add(n_poles * reach.numerator >= width * reach.denominator)
    else:
        den = math.lcm(*(pt.reach.denominator for pt in tiers))
        m.add(sum(int(tiers[q[0]].reach * den) * v for q, v in Q.items()) >= width * den)
    m.add(n_poles >= 1)
    for a in qs:
        nbr[a] = [
            b
            for b in qs
            if b != a
            and (cen[a][0] - cen[b][0]) ** 2 + (cen[a][1] - cen[b][1]) ** 2 <= d2(a[0], b[0])
        ]
    # some pole reaches the image (x + W) of some pole: the pole k columns left of the
    # right edge (W - 1 - k) against a pole near the left edge
    kmax = math.ceil(ctx.wire)
    edge: dict[tuple[int, int, int], list[Any]] = defaultdict(list)  # (tier, k, y) -> z vars
    for (ti, x, y), qv in Q.items():
        for k in range(min(kmax, math.ceil(tiers[ti].wire)) + 1):
            v = x + 1 + k
            if v < w_min or v > w:
                continue
            z = m.new_bool_var("")
            m.add(z <= qv)
            m.add(z <= eq(v))
            edge[(ti, k, y)].append(z)
    cross = []
    if len(tiers) == 1:
        for (ta, k, ya), zs in edge.items():
            ca = tiers[ta].c
            for (tb, xb, yb), qv in Q.items():
                cb = tiers[tb].c
                dx = (xb + 1 + k) + cb[0] - ca[0]
                dy = (yb + cb[1]) - (ya + ca[1])
                if dx**2 + dy**2 <= d2(ta, tb):
                    c = m.new_bool_var("")
                    m.add(c <= sum(zs))
                    m.add(c <= qv)
                    cross.append(c)
    else:
        # palette: the left pole only needs x <= X (a prefix of its row), so one literal
        # per (left tier, row, X) "some pole there" instead of one per pole pair
        pre: dict[tuple[int, int, int], Any] = {}
        for (tb, xb, yb), qv in Q.items():
            v = m.new_bool_var("")
            m.add(v <= pre.get((tb, yb, xb - 1), 0) + qv)
            pre[(tb, yb, xb)] = v
        last = {(tb, yb): xb for tb, xb, yb in Q}
        for (ta, k, ya), zs in edge.items():
            ca = tiers[ta].c
            for (tb, yb), xl in last.items():
                cb = tiers[tb].c
                dy = (yb + cb[1]) - (ya + ca[1])
                if dy**2 > d2(ta, tb):
                    continue
                xm = math.floor(math.sqrt(d2(ta, tb) - dy**2) - 1 - k - cb[0] + ca[0] + 1e-9)
                if xm < 0:
                    continue
                c = m.new_bool_var("")
                m.add(c <= sum(zs))
                m.add(c <= pre[(tb, yb, min(xm, xl))])
                cross.append(c)
    if not cross:
        return None
    m.add(sum(cross) >= 1)
    return n_poles


class _BeltPal:
    """Belt-row palette variables of one model: ``bt[b][t]`` (row ``b`` has tier ``t``;
    None with one tier) and per row with undergrounds ``ugi``/``ugo``/``gap`` per column."""

    def __init__(self, bt: list[list[Any]] | None) -> None:
        self.bt = bt
        self.ugi: dict[int, list[Any]] = {}
        self.ugo: dict[int, list[Any]] = {}
        self.gap: dict[int, list[Any]] = {}

    def off(self, k: int, x: int) -> Any:
        """1 iff column ``x`` of row ``k`` is no plain belt (None: always plain)."""
        if k not in self.ugi:
            return None
        return self.ugi[k][x] + self.ugo[k][x] + self.gap[k][x]

    def cap(self, k: int, caps: list[int]) -> Any:
        """Row ``k``'s value of a per-tier number."""
        if self.bt is None:
            return caps[0]
        return sum(c * v for c, v in zip(caps, self.bt[k], strict=True))


def _carries(ctx: _Ctx, b: SkeletonBelt, cap: float) -> bool:
    """A belt of lane capacity ``cap`` carries ``ctx.repeats`` periods of ``b``."""
    if b.role == "output":
        return ctx.flows.output_rate[b.materials[0]] * ctx.repeats <= cap * ctx.lanes + 1e-9
    n = 1 if len(b.materials) > 1 else ctx.lanes
    return all(belt_rate(ctx.flows, mat) * ctx.repeats <= cap * n + 1e-9 for mat in b.materials)


def _belt_palette(
    m: Any,
    ctx: _Ctx,
    h: int,
    w: int,
    u: list[Any],
    belts: list[SkeletonBelt],
    row: list[list[Any]],
    occ: dict[tuple[int, int], list[Any]],
) -> _BeltPal:
    """Belt tiers per row and underground pairs in the rows (plan-phase4c); fills the
    rows' tile occupancy (a row's tile is free where it runs underground)."""
    nb, nt = len(belts), len(ctx.belt_tiers)
    bt = None
    if nt > 1:
        bt = [[m.new_bool_var(f"bt{b}_{t}") for t in range(nt)] for b in range(nb)]
        for b, sk in enumerate(belts):
            m.add_exactly_one(bt[b])
            for t, cap in enumerate(ctx.lane_caps):
                if not _carries(ctx, sk, cap):
                    m.add(bt[b][t] == 0)
    pal = _BeltPal(bt)
    if any(ctx.ug):
        for b in range(nb):
            ugi = [m.new_bool_var(f"ugi{b}_{x}") for x in range(w)]
            ugo = [m.new_bool_var(f"ugo{b}_{x}") for x in range(w)]
            gap = [m.new_bool_var(f"gap{b}_{x}") for x in range(w)]
            for x in range(w):
                # entrance, free tiles, exit; never the first or the last column
                if x == 0 or x + 1 >= w:
                    m.add(ugi[x] + ugo[x] + gap[x] == 0)
                    continue
                m.add(ugi[x] + ugo[x] + gap[x] <= u[x + 1])
                m.add(gap[x] <= ugi[x - 1] + gap[x - 1])
                m.add(ugo[x] <= ugi[x - 1] + gap[x - 1])
                m.add(gap[x] <= ugo[x + 1] + gap[x + 1])
                m.add(ugi[x] <= ugo[x + 1] + gap[x + 1])
            for t in range(nt):
                on: Any = 1 if bt is None else bt[b][t]
                if not ctx.ug[t]:
                    if bt is not None:
                        m.add(sum(ugi) + sum(ugo) + sum(gap) <= 3 * w * (1 - bt[b][t]))
                    continue
                r = ctx.ug_reach[t]  # entrance to exit at most r: no r free tiles in a row
                for x0 in range(w - r + 1):
                    m.add(sum(gap[x0 : x0 + r]) <= r - 1 + r * (1 - on))
            pal.ugi[b], pal.ugo[b], pal.gap[b] = ugi, ugo, gap
    for y in range(h):
        for x in range(w):
            for b in range(nb):
                if b not in pal.gap:
                    occ[(x, y)].append(row[b][y])
                    continue
                g = m.new_bool_var("")  # row b on y and running underground at x
                m.add(g <= row[b][y])
                m.add(g <= pal.gap[b][x])
                m.add(g >= row[b][y] + pal.gap[b][x] - 1)
                occ[(x, y)].append(row[b][y] - g)
    return pal


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
    preset: str | None = None,
    bound: int | None = None,
    det: float | None = None,
    aggregate: bool = True,
    prune: bool = False,
) -> tuple[str, Period | None, float]:
    """One CP-SAT model for height ``h`` and widths ``w_min..w`` (the width is a
    variable); ``optimize``: minimise width, poles, inserters (or the ``preset`` key),
    else stop at the first feasible period; without ``power`` the poles are left out (a
    relaxation: only its infeasibility means anything). ``bound``: the preset key must be
    at most this (beat an incumbent). ``det``: deterministic time limit. Returns (status
    name, period, objective: the preset key of the period when ``preset`` is given)."""
    cp_model = _cp()
    spec, f = ctx.spec, ctx.flows
    rows_ok: set[tuple[int, int]] | None = None
    anchor_rows: list[set[int]] = []
    if prune:  # plan-phase8 C: rows no assignment of the belts allows (ctx.belt_rows)
        allowed = ctx.belt_rows(h, belts)
        if allowed is None:
            stats.pruned["static_rows_infeasible"] += 1
            if DEBUG:
                print(f"  h={h} {[b.materials for b in belts]} static rows INFEASIBLE", flush=True)
            return "INFEASIBLE", None, math.inf
        rows_ok, anchor_rows = allowed
    t_build = time.monotonic()
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
    m.add(2 * sum(y * row[0][y] for y in range(h)) <= h - 1)
    if rows_ok is not None:
        for b in range(nb):
            for y in range(h):
                if (b, y) not in rows_ok:
                    m.add(row[b][y] == 0)
    in_belts = {k: b for k, b in enumerate(belts) if b.role == "input"}
    out_belts = {k: b for k, b in enumerate(belts) if b.role == "output"}
    pal = _belt_palette(m, ctx, h, w, u, belts, row, occ)

    # machines
    stages = range(len(spec.stages))
    P: list[dict[tuple[int, int], Any]] = []
    cover: list[dict[tuple[int, int], list[tuple[tuple[int, int], Any]]]] = []
    for si in stages:
        mw, mh = ctx.msize[si]
        ps: dict[tuple[int, int], Any] = {}
        cv: dict[tuple[int, int], list[tuple[tuple[int, int], Any]]] = defaultdict(list)
        for y in range(h - mh + 1):
            if rows_ok is not None and y not in anchor_rows[si]:
                continue
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
                if py == y or (rows_ok is not None and (k, py) not in rows_ok):
                    continue
                tgt = covered(users[k], (dx, dy))
                if not tgt:
                    continue
                v = m.new_bool_var("")
                m.add(v <= inside)
                m.add(v <= row[k][py])
                m.add(v <= sum(tgt))
                if (o := pal.off(k, px)) is not None:
                    m.add(v + o <= 1)  # only plain belt tiles hand items over
                ins_at[(x, y)].append(v)
                in_drop[(k, (dx, dy))].append((v, cap))
                if k in lane_belts:
                    lane_pick[k].append((v, px))
                slots.append((v, PlacedInserter(x, y, d, tier, "in", -1, k), (px, py), (dx, dy)))
            for k in out_belts:
                if dx != x or dy == y or (rows_ok is not None and (k, dy) not in rows_ok):
                    continue
                src = covered(makers[k], (px, py))
                if not src:
                    continue
                v = m.new_bool_var("")
                m.add(v <= inside)
                m.add(v <= row[k][dy])
                m.add(v <= sum(src))
                if (o := pal.off(k, dx)) is not None:
                    m.add(v + o <= 1)
                approach = Direction.S if dy > y else Direction.N
                lane = beltgeom.drop_lane(FLOW, approach)
                ins_at[(x, y)].append(v)
                out_pick[(px, py)].append((v, cap, k, lane))
                slots.append(
                    (v, PlacedInserter(x, y, d, tier, "out", -1, k, lane), (px, py), (dx, dy))
                )
            for key in lane_keys:
                k = key[0]
                if dx != x or dy == y or (rows_ok is not None and (k, dy) not in rows_ok):
                    continue
                src = covered(lane_makers[key], (px, py))
                if not src:
                    continue
                v = m.new_bool_var("")
                m.add(v <= inside)
                m.add(v <= row[k][dy])
                m.add(v <= sum(src))
                if (o := pal.off(k, dx)) is not None:
                    m.add(v + o <= 1)
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

    # intermediates: flow per producer/consumer anchor pair. A stage with one machine
    # needs no anchor index in its machine-to-machine flows (plan-phase8 C,
    # ``aggregate``): which machine of it gives or takes is unique (a slot's
    # ``v <= sum(src)``/``v <= sum(tgt)`` already put both ends on machines of the right
    # stages), so those flows, and its supply and demand, are per stage; the same set of
    # periods with far fewer variables (green science: 13248 flows -> 2). Stages in no
    # such pair keep their model exactly as it was.
    linked = {si for pr in pairs for si in pr}
    one = [aggregate and s.count == 1 and si in linked for si, s in enumerate(spec.stages)]

    def idx(si: int, a: tuple[int, int]) -> tuple[int, int] | None:
        return None if one[si] else a

    def on(si: int, a: tuple[int, int] | None) -> Any:
        return 1 if a is None else P[si][a]

    Key = tuple[int, int] | None
    pair_terms: dict[tuple[int, int, Key, Key], list[Any]] = defaultdict(list)
    for v, ps, cs, pt, dt, pi in link_vars:
        cap = _q(ctx.caps[pi.tier])
        aps = dict.fromkeys(idx(ps, ap) for ap, _ in cover[ps][pt])
        acs = dict.fromkeys(idx(cs, ac) for ac, _ in cover[cs][dt])
        for ap in aps:
            for ac in acs:
                pair_terms[(ps, cs, ap, ac)].append((v, cap))
    give: dict[tuple[int, Key], list[Any]] = defaultdict(list)
    take: dict[tuple[int, str, Key], list[Any]] = defaultdict(list)
    for (ps, cs, ap, ac), terms in pair_terms.items():
        if ap is not None and ac is not None and set(fp(ps, ap)) & set(fp(cs, ac)):
            continue
        item = f.stages[ps].product
        hi = _q(min(f.stages[ps].rate, f.stages[cs].internal_in[item]))
        fv = m.new_int_var(0, hi, "")
        m.add(fv <= sum(c * v for v, c in terms))
        if ap is not None:
            m.add(fv <= hi * P[ps][ap])
        if ac is not None:
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
        m.add(sum(made) <= pal.cap(k, [_q(c) for c in ctx.lane_caps]))
        last = m.new_int_var(0, w, "")
        for v, x in lane_put.get(key, []):
            m.add(last >= x * v)
        for v, x in lane_pick.get(k, []):
            m.add(x >= last - w * (1 - v))

    def anchors_of(si: int) -> list[Key]:
        return [None] if one[si] else list(P[si])

    def gives(si: int, a: Key) -> list[Any]:
        """What a machine hands on; a single machine's by stage, its lane drops too."""
        if a is not None:
            return give.get((si, a), [])
        return [*give.get((si, None), []), *(g for ap in P[si] for g in give.get((si, ap), []))]

    for ps in stages:
        if f.stages[ps].final:
            continue
        for ap in anchors_of(ps):
            if gives(ps, ap):
                m.add(sum(gives(ps, ap)) <= _q(f.stages[ps].rate) * on(ps, ap))
    # consumers: a final machine runs at full speed; an intermediate one needs its
    # ingredients in proportion to what it hands on (multi-level trees with surplus)
    for cs in stages:
        csf = f.stages[cs]
        for item, dem in csf.internal_in.items():
            if item in lane_set:
                continue  # taken from the belt with the raw inputs (need_in)
            for ac in anchors_of(cs):
                got = take.get((cs, item, ac), [])
                if csf.final:
                    m.add(sum(got) >= _q(dem) * on(cs, ac))
                else:
                    m.add(_q(csf.rate) * sum(got) >= _q(dem) * sum(gives(cs, ac)))

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
        caps_l = [math.floor(_q(c) / ctx.repeats) for c in ctx.lane_caps]
        for (k, _ln), shares in lane_load.items():
            m.add(sum(shares) <= pal.cap(k, caps_l))

    Q: dict[tuple[int, int, int], Any] = {}
    nbr: dict[tuple[int, int, int], list[tuple[int, int, int]]] = {}
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
    if preset is not None and (optimize or bound is not None):
        key = _preset_key(m, ctx, h, w, width, preset, slots, Q, n_poles, n_ins, pal, fv, nb)
        if bound is not None:
            m.add(key <= bound)
        if optimize:
            m.minimize(key)
    elif optimize:
        m.minimize(WIDTH_WEIGHT * width + POLE_WEIGHT * n_poles + n_ins)

    stats.variables["cp_vars_max"] = max(
        stats.variables.get("cp_vars_max", 0), len(m.proto.variables)
    )
    stats.variables["cp_vars_last"] = len(m.proto.variables)
    stats.variables["cp_constraints_max"] = max(
        stats.variables.get("cp_constraints_max", 0), len(m.proto.constraints)
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.01, seconds)
    solver.parameters.num_workers = workers
    # deterministic parallel search: the same spec always gives the same period (the
    # blueprint sha keeps measurements across regenerations)
    solver.parameters.interleave_search = True
    # not optimize: no objective, so CP-SAT returns at its first solution by itself
    # (``stop_after_first_solution`` with ``interleave_search`` aborts OR-Tools 9.15 on
    # some models: "Check failed: heuristics.fixed_search != nullptr", plan-phase4c)
    for k, v in PARAMS.items():  # experiments (plan-phase4c record)
        setattr(solver.parameters, k, v)
    # the poles must be one wire network: solved without it, then for every pole set
    # that splits, a cut "some pole on the boundary of this component" is added
    t_solve = time.monotonic()
    stats.nodes["cp_build_ms"] += round((t_solve - t_build) * 1000)
    t_end = t_solve + seconds
    det_used = 0.0
    while True:
        solver.parameters.max_time_in_seconds = max(0.01, t_end - time.monotonic())
        if det is not None:
            solver.parameters.max_deterministic_time = max(0.01, det - det_used)
        status = solver.solve(m)
        det_used += solver.deterministic_time
        stats.nodes["cp_det_ms"] += round(solver.deterministic_time * 1000)
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
    stats.nodes["cp_solve_ms"] += round((time.monotonic() - t_solve) * 1000)
    if DEBUG:
        print(
            f"  h={h} w={w_min}..{w} {'' if power else 'screen '}{[b.materials for b in belts]} "
            f"{name} build {t_solve - t_build:.2f}s solve {time.monotonic() - t_solve:.2f}s "
            f"det {det_used:.2f} vars {len(m.proto.variables)} branches {solver.num_branches}",
            flush=True,
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
    poles = [(ctx.pole_tiers[q[0]].name, q[1], q[2]) for q, v in Q.items() if solver.value(v)]
    w_sol = sum(solver.value(v) for v in u)
    tiers = [
        ctx.belt_tiers[
            0 if pal.bt is None else next(t for t, v in enumerate(pal.bt[b]) if solver.value(v))
        ]
        for b in range(nb)
    ]
    ugs: dict[int, dict[int, str]] = {}
    for b in pal.ugi:
        for x in range(w_sol):
            for kind, vs in (("input", pal.ugi), ("output", pal.ugo), ("gap", pal.gap)):
                if solver.value(vs[b][x]):
                    ugs.setdefault(b, {})[x] = kind
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
        tiers,
        ugs,
    )
    per.meta["proved_optimal"] = status == cp_model.OPTIMAL or not optimize
    if preset is not None and ctx.scale is not None:
        mt = presets.metrics(per, ctx.gd, ctx.scale)
        per.meta["metrics"] = mt
        return name, per, ctx.scale.key(preset, mt)
    return name, per, solver.objective_value if optimize else math.inf


def _preset_key(
    m: Any,
    ctx: _Ctx,
    h: int,
    w: int,
    width: Any,
    preset: str,
    slots: list[tuple[Any, PlacedInserter, tuple[int, int], tuple[int, int]]],
    Q: dict[tuple[int, int, int], Any],
    n_poles: Any,
    n_ins: Any,
    pal: _BeltPal,
    fv: Any,
    n_rows: int,
) -> Any:
    """The preset's integer key (:mod:`fgen.solve.preset`) as a linear expression."""
    sc = ctx.scale
    assert sc is not None
    terms = presets.PRESETS[preset]
    val: dict[str, Any] = {"area": h * width, "poles": n_poles, "inserters": n_ins}
    by_tier: dict[str, list[Any]] = defaultdict(list)
    for v, pi, _, _ in slots:
        by_tier[pi.tier].append(v)
    for q, v in Q.items():
        by_tier[ctx.pole_tiers[q[0]].name].append(v)

    def plain(b: int) -> Any:
        if b not in pal.gap:
            return width
        return width - sum(pal.off(b, x) for x in range(w))

    def under(b: int) -> Any:
        return sum(pal.ugi[b]) + sum(pal.ugo[b]) if b in pal.ugi else 0

    if "cost" in terms:
        c = sum(sc.cost[t] * sum(vs) for t, vs in by_tier.items())
        for b in range(n_rows):
            for t, name in enumerate(ctx.belt_tiers):
                ug = ctx.ug[t]
                if pal.bt is None:
                    c += sc.cost[name] * plain(b) + (sc.cost[ug] * under(b) if ug else 0)
                    continue
                z = m.new_int_var(0, w, "")
                m.add(z >= plain(b) - w * (1 - pal.bt[b][t]))
                c += sc.cost[name] * z
                if ug and b in pal.ugi:
                    zu = m.new_int_var(0, w, "")
                    m.add(zu >= under(b) - w * (1 - pal.bt[b][t]))
                    c += sc.cost[ug] * zu
        if fv is not None:
            c += sc.cost[ctx.spec.pipe] * sum(fv.pipe.values())
            if ctx.spec.pipe_to_ground:
                c += sc.cost[ctx.spec.pipe_to_ground] * sum(fv.ptg.values())
        val["cost"] = c
    if "kinds" in terms:
        k: Any = 0
        groups = list(by_tier.values())
        if fv is not None:
            groups += [list(fv.pipe.values()), list(fv.ptg.values())]
        for vs in groups:
            if vs:
                used = m.new_bool_var("")
                m.add(sum(vs) <= len(vs) * used)
                k += used
        for t in range(len(ctx.belt_tiers)):
            ug = ctx.ug[t]
            if pal.bt is None:
                k += 1
            else:
                used = m.new_bool_var("")
                for b in range(n_rows):
                    m.add(used >= pal.bt[b][t])
                k += used
            if ug and pal.ugi:
                uu = m.new_bool_var("")
                for b in pal.ugi:
                    on: Any = 1 if pal.bt is None else pal.bt[b][t]
                    m.add(under(b) <= w * (uu + 1 - on))
                k += uu
        val["kinds"] = k
    wt = sc.weights(preset)
    return sum(wt[t] * val[t] for t in terms)


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
    preset: str = "compact",
    prove: bool = False,
    deterministic: bool = True,
    **_exhaustive_only: Any,
) -> SolveResult:
    """Periods for ``spec``, best first by the ``preset`` (:mod:`fgen.solve.preset`).

    ``prove=True``: the best is proven (``compact``: the phase-4b driver, the same
    contract as :func:`fgen.solve.cycle.solve`). Default: the anytime driver of the
    module docstring within ``time_limit`` seconds (``deterministic``: the slices are
    CP-SAT deterministic time, so the same spec gives the same period; the wall clock
    still caps the whole call at ``time_limit``)."""
    _cp()
    if preset not in presets.PRESETS:
        raise ValueError(f"preset {preset!r}: one of {sorted(presets.PRESETS)}")
    t0 = time.monotonic()
    stats = SolveStats()
    spec, gone = undominated(spec, gd)
    for n in gone:
        stats.pruned[f"dominated:{n}"] += 1
    ctx = _Ctx(spec, gd, repeats)
    ctx.scale = presets.scale_for(gd, ctx.palette_names(), max_width, max_height)
    stats.variables.update(machines=spec.machines(), min_inserters=ctx.min_inserters)
    all_sets = belt_sets(ctx.flows, ctx.lanes, spec.lane_items)
    skeletons = [
        bs for bs in all_sets if input_lanes_ok(bs, ctx.flows, ctx.lane_cap, ctx.lanes, repeats)
    ]
    stats.pruned["lane_capacity"] += len(all_sets) - len(skeletons)
    if not ctx.slots_ok():
        stats.pruned["slot_count"] += 1
        skeletons = []
    if prove and preset == "compact":
        found = _proved(
            ctx, skeletons, stats, t0, time_limit, workers, screen_seconds, max_width, max_height
        )
        finish_stats(stats, found)
        sc = ctx.scale
        for per in found:
            per.meta.setdefault("metrics", presets.metrics(per, gd, sc))
        return SolveResult(found[:candidates], stats)
    # deterministic: the box becomes a CP-SAT deterministic-time budget (presolve and
    # model building cost wall time on top of it: DET_PER_SECOND, measured); the wall
    # clock only caps runaways
    wall_end = t0 + time_limit * (WALL_FACTOR if deterministic else 1)
    box = time_limit * DET_PER_SECOND if deterministic else time_limit
    args = (workers, screen_seconds, max_width, max_height, preset, prove, deterministic)
    seed = None
    spent = 0.0
    g = math.gcd(*(st.count for st in spec.stages))
    if not prove and g > 1:
        # sub-period tiling: every count is g times a smaller spec's, so g copies of its
        # period side by side are a period of this spec (each copy feeds itself, as the
        # copies of a unit do); the smaller spec is solved first and seeds this one
        sub = replace(spec, stages=tuple(replace(st, count=st.count // g) for st in spec.stages))
        r0 = solve(
            sub,
            gd,
            repeats=repeats * g,
            max_width=max_width // g,
            max_height=max_height,
            time_limit=SUB_SHARE * time_limit,
            workers=workers,
            screen_seconds=screen_seconds,
            preset=preset,
            deterministic=deterministic,
        )
        for k, v in r0.stats.pruned.items():
            stats.pruned[f"sub_{k}"] += v
        spent += r0.stats.variables.get("budget_spent_ms", 0) / 1000
        if r0.best is not None:
            per = repeat_period(r0.best, g, spec, gd)
            belts = [SkeletonBelt(b.role, tuple(sorted(b.materials()))) for b in per.belts]
            match = next(
                (b for b in skeletons if sorted(map(_sk, b)) == sorted(map(_sk, belts))), None
            )
            if match is not None and not check_period(per, gd):
                per.meta["seconds_to_find"] = round(time.monotonic() - t0, 3)
                per.meta["metrics"] = presets.metrics(per, gd, ctx.scale)
                seed = (ctx.scale.key(preset, per.meta["metrics"]), per, match)
                stats.phases["first_feasible_s"] = per.meta["seconds_to_find"]
                stats.phases["area_s"] = stats.phases["objective_s"] = stats.phases[
                    "first_feasible_s"
                ]
    narrow = replace(spec, extra_inserters=(), extra_poles=(), extra_belts=(), undergrounds=())
    if not prove and narrow != spec and seed is None:
        # staged palette: the primary tiers alone first (a smaller model; any period of a
        # sub-palette is a period of the palette), its best seeds the full palette; it
        # gives up as soon as its loose sizes found nothing (the primary tiers may have
        # no period at all: green science needs long-handed inserters)
        nctx = _Ctx(narrow, gd, repeats)
        nctx.scale = ctx.scale
        nsk = [
            b
            for b in skeletons
            if input_lanes_ok(b, nctx.flows, nctx.lane_cap, nctx.lanes, repeats)
        ]
        f0, spent0, _ = _anytime(
            nctx, nsk, stats, t0, wall_end, STAGE0_SHARE * box, *args, give_up=True
        )
        spent += spent0
        seed = f0[0] if f0 else None
        stats.timed_out = False
    found, _, closed = _anytime(ctx, skeletons, stats, t0, wall_end, box - spent, *args, seed=seed)
    stats.seconds = time.monotonic() - t0
    stats.proven = closed and not stats.timed_out
    stats.unproven_infeasible = not found and not stats.proven
    stats.phases["total_s"] = round(stats.seconds, 3)
    stats.variables["det_time_ms"] = stats.nodes["cp_det_ms"]
    out = [per for _, per, _ in found]
    for per in out:
        per.meta["proved_optimal"] = stats.proven and per is out[0]
    return SolveResult(out[:candidates], stats)


def undominated(spec: CycleSpec, gd: GameData) -> tuple[CycleSpec, list[str]]:
    """Drop palette entries another entry dominates for every preset: an inserter with
    the same pickup and drop tiles in every direction, no more capacity and a higher
    cost (no-research bulk inserter vs fast inserter); a pole with no smaller footprint,
    no larger supply or wire reach and a higher cost. The primary tiers stay."""
    from fgen.data.cost import entity_cost

    c0 = Tile(0, 0).center()

    def geo(n: str) -> tuple[Any, ...]:
        return tuple(
            (inserter.pickup_tile(gd, n, c0, d), inserter.drop_tile(gd, n, c0, d))
            for d in CARDINALS
        )

    def ins_dom(a: str, b: str) -> bool:  # a dominates b
        return (
            geo(a) == geo(b)
            and tp.inserter_throughput_estimate(gd, a) >= tp.inserter_throughput_estimate(gd, b)
            and entity_cost(gd, a) < entity_cost(gd, b)
        )

    def pole_dom(a: str, b: str) -> bool:
        fa, fb = footprint.size(gd, a), footprint.size(gd, b)
        ea, eb = gd.entity(a), gd.entity(b)
        return (
            fa[0] <= fb[0]
            and fa[1] <= fb[1]
            and (ea.supply_area_distance or 0) >= (eb.supply_area_distance or 0)
            and pole.wire_distance(gd, a, a) >= pole.wire_distance(gd, b, b)
            and entity_cost(gd, a) < entity_cost(gd, b)
        )

    gone: list[str] = []

    def keep(primary: str, extra: tuple[str, ...], dom: Any) -> tuple[str, ...]:
        pal = (primary, *extra)
        out = []
        for n in extra:
            if n != primary and any(dom(o, n) for o in pal if o != n):
                gone.append(n)
            else:
                out.append(n)
        return tuple(out)

    if len(spec.inserters) > 1 or len(spec.poles) > 1:
        try:
            spec = replace(
                spec,
                extra_inserters=keep(spec.inserter, spec.extra_inserters, ins_dom),
                extra_poles=keep(spec.pole, spec.extra_poles, pole_dom),
            )
        except ValueError:  # an entity without a recipe: no cost, nothing dominated
            return spec, []
    return spec, gone


def _sk(b: SkeletonBelt) -> tuple[str, tuple[str, ...]]:
    return (b.role, tuple(sorted(b.materials)))


def repeat_period(per: Period, g: int, spec: CycleSpec, gd: GameData) -> Period:
    """``g`` copies of ``per`` side by side as one period of ``spec`` (whose counts are
    ``g`` times ``per``'s): rates per period scale by ``g``."""
    from fgen.layout.model import Layout

    lay: Layout = tile(per, g)
    rows = tuple(replace(b, rates=tuple((ln, v * g) for ln, v in b.rates)) for b in per.belts)
    pipes = tuple(replace(c, rate=c.rate * g) for c in per.pipes)
    meta = {
        **{k: v for k, v in per.meta.items() if k not in ("metrics",)},
        "inserters": per.meta["inserters"] * g,
        "poles": per.meta["poles"] * g,
        "pipes": per.meta.get("pipes", 0) * g,
        "links": per.meta.get("links", []),  # per copy
        "sub_period": {"copies": g, "width": per.width},
    }
    return Period(
        per.width * g,
        per.height,
        tuple(lay.placements),
        rows,
        spec,
        per.stage_of * g,
        meta,
        pipes,
        per.fluid_of * g,
    )


def _bounds(ctx: _Ctx, h: int, belts: list[SkeletonBelt], w_hi: int) -> int:
    """Smallest width worth a model at height ``h``: belt rows + machine tiles + fewest
    inserters + one pole must fit (``w_hi + 1``: none)."""
    mw = max(w for w, _ in ctx.msize)
    fixed = ctx.machine_tiles + ctx.min_inserters + 1
    return max(mw, math.ceil(fixed / (h - len(belts)))) if h > len(belts) else w_hi + 1


def _proved(
    ctx: _Ctx,
    skeletons: list[list[SkeletonBelt]],
    stats: SolveStats,
    t0: float,
    time_limit: float,
    workers: int,
    screen_seconds: float,
    max_width: int,
    max_height: int,
) -> list[Period]:
    """The phase-4b driver (``compact``, proven): heights up, the pole-free screen, then
    fixed widths up, the first feasible one optimal for its height. Independent models
    (the screens of one height, the fixed widths in chunks of ``PROCS``) run in forked
    processes (plan-phase7 B): every model is the same deterministic CP-SAT solve, only
    the wall clock changes."""
    deadline = t0 + time_limit
    mh = max(h for _, h in ctx.msize)
    found: list[tuple[float, Period]] = []
    best_area: int | None = None
    pool = _Pool(ctx, workers, stats)
    try:
        for h in range(mh + 1, max_height + 1):
            ts = time.monotonic()
            n_found = 0
            w_hi = max_width if best_area is None else min(max_width, best_area // h)
            todo = []
            for belts in skeletons:
                w_lo = _bounds(ctx, h, belts, w_hi)
                if w_lo > w_hi:
                    stats.pruned["area_bound"] += 1
                    continue
                todo.append((belts, w_lo))
            left = deadline - time.monotonic()
            if left <= 0 and todo:
                stats.timed_out = True
                break
            # screen every width of this height at once, without the poles (a relaxation:
            # infeasible there = infeasible), every skeleton side by side
            screens = pool.run(
                [
                    dict(h=h, w_min=w_lo, w=w_hi, belts=belts, seconds=min(left, screen_seconds))
                    for belts, w_lo in todo
                ],
                optimize=False,
                power=False,
                prune=True,
            )
            for (belts, w_lo), (status, _, _) in zip(todo, screens, strict=True):
                stats.pruned[f"cp_screen_{status.lower()}"] += 1
                if status == "INFEASIBLE":
                    continue
                w_lo = _bounds(ctx, h, belts, w_hi)  # w_hi may have shrunk
                if w_lo > w_hi:
                    stats.pruned["area_bound"] += 1
                    continue
                # the fixed widths smallest first, in chunks solved side by side for a
                # first period each (a feasible width is cheap to show, its optimum is
                # not): the smallest feasible one is the height's width, as in the
                # sequential scan; then that width's phase-4b model with the objective
                per, obj, w_min = None, math.inf, None
                for w0 in range(w_lo, w_hi + 1, pool.procs):
                    left = deadline - time.monotonic()
                    if left <= 0:
                        stats.timed_out = True
                        break
                    ws = list(range(w0, min(w0 + pool.procs, w_hi + 1)))
                    outs = pool.run(
                        [dict(h=h, w_min=wf, w=wf, belts=belts, seconds=left) for wf in ws],
                        optimize=False,
                        prune=True,
                    )
                    for wf, (status, per2, _) in zip(ws, outs, strict=True):
                        stats.pruned[f"cp_first_{status.lower()}"] += 1
                        if per2 is not None:
                            w_min = wf
                            break
                        if status != "INFEASIBLE":
                            stats.timed_out = True
                            break
                    if w_min is not None or stats.timed_out:
                        break
                if w_min is not None and not stats.timed_out:
                    left = deadline - time.monotonic()
                    # the period kept: the phase-4b model as it was (no aggregation, no
                    # row pruning), so the characterized blueprints stay put
                    status, per, obj = _solve_height(
                        ctx,
                        h,
                        w_min,
                        w_min,
                        belts,
                        left,
                        workers,
                        stats,
                        optimize=True,
                        aggregate=False,
                    )
                    stats.pruned[f"cp_{status.lower()}"] += 1
                    if status == "UNKNOWN" or (per is not None and not per.meta["proved_optimal"]):
                        stats.timed_out = True
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
    finally:
        pool.close()
    stats.seconds = time.monotonic() - t0
    # the candidates in the key's order (area, poles, inserters, then cost); the models
    # above minimise the phase-4b objective, which leaves a tier tie to the solver
    sc = ctx.scale
    assert sc is not None
    for _, per in found:
        per.meta.setdefault("metrics", presets.metrics(per, ctx.gd, sc))
    found.sort(key=lambda op: sc.key("compact", op[1].meta["metrics"]))
    return [p for _, p in found]


PROCS = 4  # proof driver: independent models solved in this many forked processes
_WORKER: dict[str, Any] = {}  # the forked workers' context (set before the fork)


def _work(task: dict[str, Any]) -> tuple[str, Period | None, float, SolveStats]:
    st = SolveStats()
    out = _solve_height(_WORKER["ctx"], stats=st, workers=_WORKER["workers"], **task)
    return (*out, st)


class _Pool:
    """Independent CP-SAT models side by side in forked processes (``PROCS``; one: in
    this process). Each model is built and solved exactly as alone, so the statuses and
    periods do not depend on the process count; the stats are merged in task order."""

    def __init__(self, ctx: _Ctx, workers: int, stats: SolveStats) -> None:
        self.stats, self.pool = stats, None
        self.procs = max(1, PROCS)
        if self.procs > 1:
            import multiprocessing as mp

            _WORKER.update(ctx=ctx, workers=workers)
            self.pool = mp.get_context("fork").Pool(self.procs)
        self.ctx, self.workers = ctx, workers

    def run(self, tasks: list[dict[str, Any]], **kw: Any) -> list[tuple[str, Period | None, float]]:
        if not tasks:
            return []
        tasks = [{**t, **kw} for t in tasks]
        if self.pool is None:
            outs = [
                (*_solve_height(self.ctx, stats=self.stats, workers=self.workers, **t), None)
                for t in tasks
            ]
        else:
            outs = self.pool.map(_work, tasks, chunksize=1)
        res = []
        for status, per, obj, st in outs:
            if st is not None:
                _merge(self.stats, st)
            res.append((status, per, obj))
        return res

    def close(self) -> None:
        if self.pool is not None:
            self.pool.terminate()
            self.pool.join()
            self.pool = None


def _merge(into: SolveStats, st: SolveStats) -> None:
    into.nodes.update(st.nodes)
    into.pruned.update(st.pruned)
    for k, v in st.variables.items():
        into.variables[k] = max(into.variables.get(k, 0), v) if k.endswith("_max") else v


# deterministic boxes: budget units (CP-SAT deterministic seconds plus the model charge
# below) per wall second, and the wall cap
DET_PER_SECOND = 1.0
# a model's Python building and presolve, charged to deterministic boxes per thousand
# variables (they are not CP-SAT deterministic time; 0.1 s per 1000 variables measured on
# the 3:2 models, 0.19 on the green-science ones, plan-phase4c record)
MODEL_COST_PER_KVAR = 0.15
WALL_FACTOR = 2.0
STAGE0_SHARE = 0.3  # share of the time box for the primary tiers before the full palette
POLISH_RESERVE = 0.2  # share of the box kept for polishing the final best (phase 3)
SUB_SHARE = 0.15  # sub-period tiling: share of the box for the smaller spec
MIN_SLICE = 1.0  # budget seconds: no model of phase 2 gets less while the box lasts
PROBE_SHARE = 0.25  # phase 2: one model may use this share of what is left
PHASE1_SHARE = 1.0  # phase 1b: the widest model of the first open height (a first period first)
LOOSE_SHARE = 0.1  # phase 1a: per loose try, share of the box
LOOSE_TOTAL = 0.3  # phase 1a: all loose tries together, share of the box


def loose_sizes(
    ctx: _Ctx, belts: list[SkeletonBelt], max_width: int, max_height: int
) -> list[tuple[int, int]]:
    """Conservative period sizes from the spec alone: the machines side by side with two
    spare columns each (link inserters, poles), in the fewest bands the width allows and
    in one band more (a lane period wants machines on both sides of its belts); each band
    one machine high plus an inserter reach on both sides; the belt rows; one pole row;
    two pipe columns per fluid. A size taller than ``max_height`` is left out."""
    span = sum((w + 2) * s.count for (w, _), s in zip(ctx.msize, ctx.spec.stages, strict=True))
    fewest = max(1, math.ceil((span + 1) / max_width))
    reach = max(max(abs(pu.y), abs(dr.y)) for _, _, pu, dr in ctx.reach)
    mh = max(h for _, h in ctx.msize)
    pipes = 2 * len(ctx.fluid.fluids) if ctx.fluid is not None else 0  # both sides
    out = []
    for bands in (fewest, fewest + 1):
        w = min(max_width, math.ceil(span / bands) + 2 + pipes)
        h = len(belts) + bands * (mh + 2 * reach) + 1
        if h <= max_height:  # clipped to the search bound it would be no estimate
            out.append((w, h))
    return list(dict.fromkeys(out))


def _anytime(
    ctx: _Ctx,
    skeletons: list[list[SkeletonBelt]],
    stats: SolveStats,
    t0: float,
    wall_end: float,
    budget: float,
    workers: int,
    screen_seconds: float,
    max_width: int,
    max_height: int,
    preset: str,
    prove: bool,
    deterministic: bool,
    seed: tuple[int, Period, list[SkeletonBelt]] | None = None,
    give_up: bool = False,
) -> tuple[list[tuple[int, Period, list[SkeletonBelt]]], float, bool]:
    """First feasible period fast, then tighten within ``budget`` (module docstring),
    starting from ``seed`` (key, period, skeleton) if given. With ``prove`` every slice
    runs to its end (a proof unless the box runs out). Returns the periods found (best
    first, with their keys and skeletons), the budget spent, whether every (height,
    skeleton) closed."""
    sc = ctx.scale
    assert sc is not None
    area_first = presets.PRESETS[preset][0] == "area"
    mh = max(h for _, h in ctx.msize)
    spent = [0.0]  # budget used: deterministic time or wall seconds

    hold = [0.0]  # budget kept back for the final polish (phase 3)

    def left() -> float:
        wall = wall_end - time.monotonic()
        return min(wall, budget - spent[0] - hold[0]) if deterministic else wall - hold[0]

    def run(h: int, lo: int, hi: int, belts: list[SkeletonBelt], secs: float, **kw: Any) -> Any:
        before = stats.nodes["cp_det_ms"]
        t = time.monotonic()
        secs = max(0.01, secs)
        wall = max(0.01, wall_end - time.monotonic())
        out = _solve_height(
            ctx,
            h,
            lo,
            hi,
            belts,
            wall if deterministic else secs,
            workers,
            stats,
            preset=preset,
            det=secs if deterministic else None,
            **kw,
        )
        if deterministic:  # solver time plus the model's building and presolve
            spent[0] += (stats.nodes["cp_det_ms"] - before) / 1000
            spent[0] += MODEL_COST_PER_KVAR * stats.variables.get("cp_vars_last", 0) / 1000
        if not deterministic:
            spent[0] += time.monotonic() - t
        stats.pruned[f"cp_{'screen_' if not kw.get('power', True) else ''}{out[0].lower()}"] += 1
        return out

    found: list[tuple[int, Period, list[SkeletonBelt]]] = [seed] if seed else []
    best: tuple[int, Period, list[SkeletonBelt]] | None = seed
    closed = True

    def take(per: Period | None, key: float, belts: list[SkeletonBelt]) -> None:
        nonlocal best
        if per is None:
            return
        now = round(time.monotonic() - t0, 3)
        per.meta["seconds_to_find"] = now
        stats.phases.setdefault("first_feasible_s", now)
        if best is None or key < best[0]:
            if best is None or per.area != best[1].area:
                stats.phases["area_s"] = now
            stats.phases["objective_s"] = now
            best = (int(key), per, belts)
            found.append(best)

    pairs = [(h, b) for h in range(mh + 1, max_height + 1) for b in skeletons]

    def w_top(h: int) -> int:
        if best is None or not area_first:
            return max_width
        return min(max_width, best[1].area // h)

    def cap(share: float = PROBE_SHARE) -> float:
        return left() if prove else min(left(), max(MIN_SLICE, share * left()))

    def bnd() -> int | None:
        return best[0] - 1 if best is not None else None

    def at(
        h: int, wf: int, belts: list[SkeletonBelt], share: float = PROBE_SHARE, **kw: Any
    ) -> Any:
        st, per, key = run(h, wf, wf, belts, cap(share), optimize=False, bound=bnd(), **kw)
        take(per, key, belts)
        return st, per

    refuted: set[tuple[int, int]] = set()  # (pair index, width) proven infeasible
    screened: dict[int, str] = {}  # pair index -> screen status

    def screen(i: int, lo: int, top: int, most: float = math.inf) -> str:
        if i not in screened or (screened[i] not in ("INFEASIBLE", "OPTIMAL")):
            h, belts = pairs[i]
            sl = min(cap(), screen_seconds, most)
            status, _, _ = run(h, lo, top, belts, sl, optimize=False, power=False)
            screened[i] = status
        return screened[i]

    # phase 1a: analytically loose sizes (:func:`loose_sizes`) for every skeleton, each
    # try capped and all of them together too (a loose model can be big: misses must
    # stay cheap)
    if best is None and ctx.fluid is None:
        stop = spent[0] + LOOSE_TOTAL * budget
        tries = [(sz, b) for b in skeletons for sz in loose_sizes(ctx, b, max_width, max_height)]
        for (wf, hf), b in sorted(tries, key=lambda t: t[0][0] * t[0][1]):
            if best is not None or left() <= 0 or spent[0] >= stop:
                break
            sl = min(left(), LOOSE_SHARE * budget, stop - spent[0])
            st, per, key = run(hf, wf, wf, b, sl, optimize=False)
            stats.pruned[f"loose_{st.lower()}"] += 1
            take(per, key, b)
        if best is None and give_up:
            return [], spent[0], False
    # phase 1b: the first feasible period: up the heights, the pole-free screen (cheap
    # proofs for the small heights), and at the first height it lets through the widest
    # period (the roomiest model of the smallest plausible height). Fluids skip this:
    # their models grow with the width, the upward width scan of phase 2 is faster.
    # Without underground belts the belt rows are whole and the static row analysis
    # (:meth:`_Ctx.belt_rows`, plan-phase8 C) does the screen's job here for free: a pair
    # it refutes is infeasible at every width, and the probe's model leaves out the rows
    # and anchors no assignment of the belts allows.
    static = not any(ctx.ug)
    if best is None and ctx.fluid is None:
        for i, (h, belts) in enumerate(pairs):
            if best is not None or left() <= 0:
                break
            lo = _bounds(ctx, h, belts, max_width)
            if lo > max_width:
                continue
            if static:
                if ctx.belt_rows(h, belts) is None:
                    screened[i] = "INFEASIBLE"
                    stats.pruned["static_rows_infeasible"] += 1
                    continue
                st, _ = at(h, max_width, belts, PHASE1_SHARE, prune=True)
            else:
                # a screen only skips hopeless heights here: it may not hold up the probe
                if screen(i, lo, max_width, 0.1 * budget) == "INFEASIBLE":
                    continue
                st, _ = at(h, max_width, belts, PHASE1_SHARE)
            if st == "INFEASIBLE":
                refuted.add((i, max_width))

    # phase 2: the incumbent's (height, skeleton) first, then up the heights. Every model
    # runs to its end, capped at a share of what is left (a restarted model pays its
    # presolve again: rounds of short slices never finished a proof, plan-phase4c
    # record). Per pair: the screen; compact: the smallest width that beats the incumbent
    # by bisection (first solutions, so every success improves the answer), the preset's
    # key at the width found is left to phase 3; then the widths
    # below it not yet refuted, upward, to close the proof; fluids or no incumbent yet:
    # widths upward (the proving order); the other presets: bisection, never closed.
    polished: set[tuple[int, int, int]] = set()  # (height, width, skeleton) polished

    def polish(h: int, belts: list[SkeletonBelt], final: bool = False) -> bool:
        if best is None or best[1].height != h or best[2] != belts or left() <= 0:
            return best is None or best[1].height != h or best[2] != belts
        wb = best[1].width
        if (h, wb, id(belts)) in polished:
            return True
        # no bound: a "key <= best - 1" row with the key's large coefficients made the
        # same model 5x slower (plan-phase4c record); take() keeps only improvements
        st, per, key = run(h, wb, wb, belts, left() if final else cap())
        polished.add((h, wb, id(belts)))
        take(per, key, belts)
        return st in ("OPTIMAL", "INFEASIBLE")

    if not prove:
        hold[0] = POLISH_RESERVE * budget
    order = list(range(len(pairs)))
    if best is not None:
        first = next(i for i, q in enumerate(pairs) if q == (best[1].height, best[2]))
        order = [first, *(i for i in order if i != first)]
    for i in order:
        h, belts = pairs[i]
        if left() <= 0:
            stats.timed_out = True
            closed = False
            break
        linear = ctx.fluid is not None or best is None
        top = w_top(h)
        lo = _bounds(ctx, h, belts, top)
        mine = best is not None and best[1].height == h and best[2] == belts
        if mine and area_first:
            top = min(top, best[1].width - 1)
        if lo > top and not mine:
            stats.pruned["area_bound"] += 1
            continue
        if lo <= top:
            status = screen(i, lo, top)
            if status == "INFEASIBLE":
                if not mine:
                    continue
                lo = top + 1  # nothing smaller here: only the incumbent left to polish
            elif status != "OPTIMAL":
                closed = False
        ok = True  # every width that could beat the incumbent here refused
        if not area_first and prove:
            for wf in range(lo, max_width + 1):
                st, per, key = run(h, wf, wf, belts, left(), bound=best[0] - 1 if best else None)
                take(per, key, belts)
                if st not in ("OPTIMAL", "INFEASIBLE"):
                    ok = False
                    break
            closed = closed and ok
            continue
        if linear:
            for wf in range(lo, top + 1):
                if (i, wf) in refuted:
                    continue
                if left() <= 0:
                    ok = False
                    break
                st, per = at(h, wf, belts)
                if per is not None:
                    break
                if st != "INFEASIBLE":
                    ok = False
                    break
                refuted.add((i, wf))
        else:
            a_, b_ = lo, top
            while a_ <= b_ and left() > 0:
                mid = (a_ + b_) // 2
                st, per = at(h, mid, belts)
                if per is not None:
                    b_ = mid - 1
                elif st == "INFEASIBLE":
                    refuted.add((i, mid))
                    a_ = mid + 1
                else:
                    ok = False
                    break
            if a_ <= b_:
                ok = False
        if area_first and not linear and ok:
            found_w = best[1].width if (best[1].height == h and best[2] == belts) else top + 1
            for wf in range(lo, found_w):  # close the proof below the width found
                if (i, wf) in refuted:
                    continue
                if left() <= 0:
                    ok = False
                    break
                st, per = at(h, wf, belts)
                if per is not None:
                    break  # a smaller one: keep it; the widths below it were refuted
                if st != "INFEASIBLE":
                    ok = False
                    break
                refuted.add((i, wf))
        if not area_first:
            ok = False  # cost or kinds first: wider may still rank better
        closed = closed and ok
    # phase 3: the preset's key (poles, inserters, cost, kinds) at the final best size,
    # with the reserve and whatever phase 2 left (intermediate incumbents are not
    # polished: they are usually superseded)
    hold[0] = 0.0
    if best is not None:
        closed = polish(best[1].height, best[2], final=True) and closed
    stats.variables["budget_spent_ms"] = stats.variables.get("budget_spent_ms", 0) + round(
        spent[0] * 1000
    )
    return sorted(found, key=lambda kp: kp[0]), spent[0], closed
