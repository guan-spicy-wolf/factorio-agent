"""Exhaustive search for one period of a direct-insertion chain (plan-phase4a §1.1).

Given a :class:`~fgen.solve.period.CycleSpec` (stages with machine counts, inserter,
pole and belt tiers) the solver looks for the smallest ``width x height`` rectangle
(area first, then height) that holds a period meeting the boundary conditions of
:mod:`fgen.solve.period` and the rates of :func:`fgen.solve.period.flows`.

Variables and their domains, searched in this order (depth first, with pruning):

1. **size** ``(W, H)``: every pair up to ``max_width x max_height`` by increasing area;
   pruned by an area lower bound (belt rows + machine tiles + the fewest inserters the
   rates need + one pole).
2. **skeleton**: which belts exist and what they carry (external inputs partitioned
   into belts of at most two lanes, one belt per output product), their top-to-bottom
   order and rows; mirror images (y flip) are skipped. Pruned by lane capacity
   (``repeats`` periods share each lane) and by a stage with too few machine anchors.
3. **machines**: an anchor per machine, final stages first; identical machines in
   increasing anchor order (symmetry). An anchor must reach every belt its stage needs
   through some inserter slot (*belt contact*); a producer must reach an already
   placed consumer through some free inserter slot (*dangling producer*). At a full
   placement: every requirement has enough free slots, and a max-flow over the
   candidate links can carry the intermediate (*flow upper bound*).
4. **inserters**: requirements (belt -> machine, machine -> output belt, producer ->
   consumer links per consumer) in order of fewest spare slots; each picks the fewest
   slots whose estimated capacity (``throughput.inserter_throughput_estimate``) covers
   its rate, one inserter per tile; after every consumer's links, a max-flow checks
   that the producers can actually split their output that way (*flow*). Output-lane
   loads are checked against lane capacity.
5. **poles**: iterative deepening on the pole count; branch on the poles that supply
   the first unsupplied consumer (most supply first), then on poles in wire reach of
   the chosen ones until they are one network that also reaches the next period.

The search is exact for the stated domains: a size is reported infeasible only after
every branch failed (or the time limit hit, which ``SolveStats.timed_out`` records).
Every number comes from ``GameData``; the solver's own checks are re-done by
:func:`fgen.solve.period.check_period` and ``validate`` on the tiled layout.
"""

from __future__ import annotations

import itertools
import math
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.geom import belt as beltgeom
from fgen.geom import footprint, inserter, pole
from fgen.geom.vec import CARDINALS, Direction, Tile
from fgen.layout.model import Placement
from fgen.solve.period import (
    FLOW,
    BeltRow,
    CycleFlows,
    CycleSpec,
    Period,
    ceil_div,
    center,
    flows,
)

_EPS = 1e-9


@dataclass
class SolveStats:
    nodes: Counter[str] = field(default_factory=Counter)  # per search level
    pruned: Counter[str] = field(default_factory=Counter)  # per constraint
    sizes: list[dict[str, Any]] = field(default_factory=list)  # per (W, H) tried
    seconds: float = 0.0
    timed_out: bool = False
    variables: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        nodes = ", ".join(f"{k} {v}" for k, v in sorted(self.nodes.items()))
        pruned = ", ".join(f"{k} {v}" for k, v in self.pruned.most_common())
        return f"{self.seconds:.2f} s; nodes: {nodes}; pruned: {pruned}"


@dataclass
class SolveResult:
    periods: list[Period]  # best first
    stats: SolveStats

    @property
    def best(self) -> Period | None:
        return self.periods[0] if self.periods else None


class _Timeout(Exception):
    pass


@dataclass(frozen=True)
class _Belt:
    role: str  # input / output
    materials: tuple[str, ...]  # input: one per lane (or one for both); output: product


@dataclass(frozen=True)
class _Slot:
    tile: int  # tile index y * W + x
    direction: Direction
    kind: str  # in / out / link
    machine: int  # target (in, link) or source (out) machine index
    other: int  # belt index (in, out) or producer machine index (link)
    lane: str | None = None  # out: lane the drop lands on


def _maxflow(cap: dict[tuple[str, str], float], s: str, t: str) -> float:
    """Edmonds-Karp on a tiny graph given as an edge -> capacity dict."""
    res: dict[str, dict[str, float]] = {}
    for (a, b), c in cap.items():
        res.setdefault(a, {}).setdefault(b, 0.0)
        res[a][b] += c
        res.setdefault(b, {}).setdefault(a, 0.0)
    total = 0.0
    while True:
        prev: dict[str, str] = {s: s}
        queue = [s]
        for a in queue:
            for b, c in res.get(a, {}).items():
                if c > _EPS and b not in prev:
                    prev[b] = a
                    queue.append(b)
        if t not in prev:
            return total
        f, b = math.inf, t
        while b != s:
            f = min(f, res[prev[b]][b])
            b = prev[b]
        b = t
        while b != s:
            res[prev[b]][b] -= f
            res[b][prev[b]] += f
            b = prev[b]
        total += f


class _Search:
    def __init__(
        self,
        spec: CycleSpec,
        gd: GameData,
        repeats: int,
        deadline: float,
        stats: SolveStats,
        inserter_budget: int = 20_000,
        pole_budget: int = 200_000,
    ) -> None:
        self.spec, self.gd, self.repeats, self.deadline, self.stats = (
            spec,
            gd,
            repeats,
            deadline,
            stats,
        )
        self.flows: CycleFlows = flows(spec, gd)
        self.cap = tp.inserter_throughput_estimate(gd, spec.inserter)
        self.lane_cap = tp.belt_lane_capacity(gd, spec.belt)
        self.lanes = int(gd.constant("belt_lanes"))
        if footprint.size(gd, spec.inserter) != (1, 1):
            raise ValueError(f"{spec.inserter}: the solver handles 1x1 inserters")
        c0 = Tile(0, 0).center()
        self.reach = {
            d: (
                inserter.pickup_tile(gd, spec.inserter, c0, d),
                inserter.drop_tile(gd, spec.inserter, c0, d),
            )
            for d in CARDINALS
        }
        self.r = max(max(abs(v.x), abs(v.y)) for pr in self.reach.values() for v in pr)
        # machines: one entry per machine of the period
        self.machine_stage: list[int] = []
        for si, s in enumerate(spec.stages):
            self.machine_stage += [si] * s.count
        sizes = {footprint.size(gd, s.machine) for s in spec.stages}
        self.msize = {si: footprint.size(gd, s.machine) for si, s in enumerate(spec.stages)}
        self.order = sorted(
            range(len(self.machine_stage)),
            key=lambda m: (not self.flows.stages[self.machine_stage[m]].final, m),
        )
        del sizes
        self.pw, self.ph = footprint.size(gd, spec.pole)
        pc = center(gd, spec.pole, 0, 0)
        self.supply = pole.supply_range(gd, spec.pole, pc)  # offsets from the anchor
        self.pole_c = (pc.x, pc.y)
        self.wire2 = pole.wire_distance(gd, spec.pole, spec.pole) ** 2 + 1e-6
        self.min_inserters = self._min_inserters()
        self.inserter_budget = inserter_budget
        self.contact_cache: dict[Any, bool] = {}
        self.pole_budget = pole_budget

    # ---- numbers ---------------------------------------------------------------------
    def _min_inserters(self) -> int:
        n = 0
        for si, s in enumerate(self.spec.stages):
            sf = self.flows.stages[si]
            per = sum(ceil_div(v, self.cap) for v in sf.internal_in.values())
            # external inputs: at least one inserter per belt, ceil(total / cap)
            per += ceil_div(sum(sf.external_in.values()), self.cap)
            if sf.final:
                per += ceil_div(sf.rate, self.cap)
            n += per * s.count
        return n

    def check_time(self) -> None:
        if time.monotonic() > self.deadline:
            raise _Timeout

    # ---- skeletons -------------------------------------------------------------------
    def belt_sets(self) -> Iterator[list[_Belt]]:
        """Input materials partitioned into belts of <= lanes materials, plus one output
        belt per product."""

        def partitions(items: list[str]) -> Iterator[list[list[str]]]:
            if not items:
                yield []
                return
            first, rest = items[0], items[1:]
            for part in partitions(rest):
                for k in range(len(part)):
                    if len(part[k]) < self.lanes:
                        yield [*part[:k], [first, *part[k]], *part[k + 1 :]]
                yield [[first], *part]

        outs = [_Belt("output", (o,)) for o in self.flows.outputs]
        for part in partitions(list(self.flows.inputs)):
            yield [_Belt("input", tuple(sorted(b))) for b in part] + outs

    def input_lanes_ok(self, belts: list[_Belt]) -> bool:
        for b in belts:
            if b.role != "input":
                continue
            lanes = 1 if len(b.materials) > 1 else self.lanes
            for m in b.materials:
                if self.flows.input_rate[m] * self.repeats > self.lane_cap * lanes + _EPS:
                    return False
        return True

    # ---- one size ----------------------------------------------------------------------
    def size(self, w: int, h: int) -> Iterator[Period]:
        self.w, self.h = w, h
        for belts in self.belt_sets():
            if not self.input_lanes_ok(belts):
                self.stats.pruned["lane_capacity"] += 1
                continue
            nb = len(belts)
            if nb * w + self.machine_tiles() + self.min_inserters + 1 > w * h:
                self.stats.pruned["area_bound"] += 1
                continue
            for perm in itertools.permutations(range(nb)):
                for ys in itertools.combinations(range(h), nb):
                    key = (tuple(belts[i] for i in perm), ys)
                    mirror = (
                        tuple(belts[i] for i in reversed(perm)),
                        tuple(h - 1 - y for y in reversed(ys)),
                    )
                    if _belt_key(mirror) < _belt_key(key):
                        self.stats.pruned["symmetry_y"] += 1
                        continue
                    self.stats.nodes["skeleton"] += 1
                    yield from self.skeleton([belts[i] for i in perm], list(ys))

    def machine_tiles(self) -> int:
        return sum(self.msize[si][0] * self.msize[si][1] for si in self.machine_stage)

    def skeleton(self, belts: list[_Belt], ys: list[int]) -> Iterator[Period]:
        w = self.w
        self.belts, self.ys = belts, ys
        self.belt_rows = {y: k for k, y in enumerate(ys)}
        belt_mask = 0
        for y in ys:
            belt_mask |= ((1 << w) - 1) << (y * w)
        self.belt_mask = belt_mask
        # which belt carries each material
        self.belt_of: dict[str, int] = {}
        for k, b in enumerate(belts):
            for m in b.materials:
                self.belt_of[(b.role, m)] = k  # type: ignore[index]
        self.anchors: dict[int, list[tuple[int, int, int, list[_Slot]]]] = {}
        for si in range(len(self.spec.stages)):
            self.anchors[si] = self.stage_anchors(si)
            need = self.spec.stages[si].count
            if len(self.anchors[si]) < need:
                self.stats.pruned["belt_contact"] += 1
                return
        self.link_cache: dict[tuple[int, int], list[tuple[int, Direction]]] = {}
        self.slot_cache: dict[tuple[int, int, int], list[_Slot]] = {}
        if not self.rows_can_link():
            self.stats.pruned["no_link_rows"] += 1
            return
        yield from self.place_machines(0, [], 0)

    def rows_can_link(self) -> bool:
        """Every producer stage has some anchor row and some consumer anchor row with an
        inserter slot between two such machines (x offsets tried within reach)."""
        for si, sf in enumerate(self.flows.stages):
            if sf.final:
                continue
            cons = [ci for ci, cf in enumerate(self.flows.stages) if sf.product in cf.internal_in]
            ok = False
            prow = {}
            for x, y, mask, _ in self.anchors[si]:
                prow.setdefault(y, (x, y, mask))
            for ci in cons:
                crow: dict[int, list[tuple[int, int, int]]] = {}
                for x, y, mask, _ in self.anchors[ci]:
                    crow.setdefault(y, []).append((x, y, mask))
                for pa in prow.values():
                    for lst in crow.values():
                        for ca in lst:
                            if (
                                abs(ca[0] - pa[0])
                                > self.msize[si][0] + self.msize[ci][0] + 2 * self.r
                            ):
                                continue
                            if ca[2] & pa[2]:
                                continue
                            if self.links(pa, ca):
                                ok = True
                                break
                        if ok:
                            break
                    if ok:
                        break
            if not ok:
                return False
        return True

    def tile_ok(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def stage_anchors(self, si: int) -> list[tuple[int, int, int, None]]:
        """(x, y, footprint mask, None) for every anchor of stage ``si`` whose row
        touches each belt the stage needs through some inserter slot."""
        w, h = self.w, self.h
        mw, mh = self.msize[si]
        sf = self.flows.stages[si]
        need_in = sorted({self.belt_of[("input", m)] for m in sf.external_in})  # type: ignore[index]
        need_out = [self.belt_of[("output", sf.product)]] if sf.final else []  # type: ignore[index]
        out: list[tuple[int, int, int, list[_Slot]]] = []
        for y in range(h - mh + 1):
            if any(yy in self.belt_rows for yy in range(y, y + mh)):
                continue
            # belt contact depends on the row only (belts span the period): test one
            # anchor first, then the whole row
            if not self.row_contact(si, y, need_in, need_out):
                self.stats.pruned["belt_contact"] += w - mw + 1
                continue
            for x in range(w - mw + 1):
                mask = 0
                for i in range(mw):
                    for j in range(mh):
                        mask |= 1 << ((y + j) * w + x + i)
                out.append((x, y, mask, None))  # belt slots: built lazily (belt_slots)
        return out

    def belt_slots(self, si: int, x: int, y: int) -> list[_Slot]:
        """Inserter slots between a stage-``si`` machine at anchor (x, y) and the belts
        it needs (``in``: belt -> machine, ``out``: machine -> output belt)."""
        key = (si, x, y)
        hit = self.slot_cache.get(key)
        if hit is not None:
            return hit
        w = self.w
        mw, mh = self.msize[si]
        sf = self.flows.stages[si]
        need_in = {self.belt_of[("input", m)] for m in sf.external_in}  # type: ignore[index]
        need_out = {self.belt_of[("output", sf.product)]} if sf.final else set()  # type: ignore[index]
        slots: list[_Slot] = []
        for tx in range(max(0, x - self.r), min(w, x + mw + self.r)):
            for ty in range(max(0, y - self.r), min(self.h, y + mh + self.r)):
                if (x <= tx < x + mw and y <= ty < y + mh) or ty in self.belt_rows:
                    continue
                idx = ty * w + tx
                for d, (pu, dr) in self.reach.items():
                    px, py = tx + pu.x, ty + pu.y
                    dx, dy = tx + dr.x, ty + dr.y
                    if not (self.tile_ok(px, py) and self.tile_ok(dx, dy)):
                        continue
                    if x <= dx < x + mw and y <= dy < y + mh and py in self.belt_rows:
                        b = self.belt_rows[py]
                        if b in need_in:
                            slots.append(_Slot(idx, d, "in", -1, b))
                    if x <= px < x + mw and y <= py < y + mh and dy in self.belt_rows:
                        b = self.belt_rows[dy]
                        if b in need_out and dx == tx:
                            approach = Direction.S if dy > ty else Direction.N
                            lane = beltgeom.drop_lane(FLOW, approach)
                            slots.append(_Slot(idx, d, "out", -1, b, lane))
        self.slot_cache[key] = slots
        return slots

    def row_contact(self, si: int, y: int, need_in: list[int], need_out: list[int]) -> bool:
        """Whether a machine of stage ``si`` with its top row at ``y`` can reach every
        belt it needs, ignoring the period's side edges."""
        mh = self.msize[si][1]
        near = tuple(
            (yy - y, self.belts[b].role, self.belts[b].materials)
            for yy, b in sorted(self.belt_rows.items())
            if y - 2 * self.r <= yy < y + mh + 2 * self.r
        )
        edge = 2 * self.r + 1
        key = (si, near, min(y, edge), min(self.h - y - mh, edge))
        hit = self.contact_cache.get(key)
        if hit is not None:
            return hit
        ok = self._row_contact(si, y, need_in, need_out)
        self.contact_cache[key] = ok
        return ok

    def _row_contact(self, si: int, y: int, need_in: list[int], need_out: list[int]) -> bool:
        mw, mh = self.msize[si]
        got: set[tuple[str, int]] = set()
        for ty in range(max(0, y - self.r), min(self.h, y + mh + self.r)):
            if ty in self.belt_rows:
                continue
            inside_rows = y <= ty < y + mh
            for pu, dr in self.reach.values():
                if not (0 <= ty + pu.y < self.h and 0 <= ty + dr.y < self.h):
                    continue
                py, dy = ty + pu.y, ty + dr.y
                # the inserter tile is outside the footprint: outside its rows, or
                # beside it with a sideways reach into it
                side_in = dr.x != 0 and abs(dr.x) <= mw
                side_out = pu.x != 0 and abs(pu.x) <= mw
                if y <= dy < y + mh and py in self.belt_rows and (not inside_rows or side_in):
                    got.add(("in", self.belt_rows[py]))
                if y <= py < y + mh and dy in self.belt_rows and (not inside_rows or side_out):
                    got.add(("out", self.belt_rows[dy]))
        return all(("in", b) in got for b in need_in) and all(("out", b) in got for b in need_out)

    def links(
        self, ap: tuple[int, int, int], ac: tuple[int, int, int]
    ) -> list[tuple[int, Direction]]:
        """Inserter slots (tile, direction) taking from machine footprint ``ap`` and
        dropping into ``ac`` (anchors as (x, y, mask)); ignores other machines."""
        key = (ap[2], ac[2])
        hit = self.link_cache.get(key)
        if hit is not None:
            return hit
        w = self.w
        out: list[tuple[int, Direction]] = []
        mp, mc = ap[2], ac[2]
        lo_x = min(ap[0], ac[0]) - self.r
        hi_x = max(ap[0], ac[0]) + 8 + self.r
        lo_y = min(ap[1], ac[1]) - self.r
        hi_y = max(ap[1], ac[1]) + 8 + self.r
        for tx in range(max(0, lo_x), min(w, hi_x)):
            for ty in range(max(0, lo_y), min(self.h, hi_y)):
                idx = ty * w + tx
                bit = 1 << idx
                if bit & (mp | mc) or ty in self.belt_rows:
                    continue
                for d, (pu, dr) in self.reach.items():
                    px, py, dx, dy = tx + pu.x, ty + pu.y, tx + dr.x, ty + dr.y
                    if not (self.tile_ok(px, py) and self.tile_ok(dx, dy)):
                        continue
                    if (mp >> (py * w + px)) & 1 and (mc >> (dy * w + dx)) & 1:
                        out.append((idx, d))
        self.link_cache[key] = out
        return out

    # ---- machines ----------------------------------------------------------------------
    def place_machines(
        self, k: int, placed: list[tuple[int, int, int, int, list[_Slot]]], occ: int
    ) -> Iterator[Period]:
        """``placed[m]`` (in ``self.order``) = (machine, x, y, mask, belt slots)."""
        self.check_time()
        if k == len(self.order):
            yield from self.full_placement(placed, occ)
            return
        m = self.order[k]
        si = self.machine_stage[m]
        sf = self.flows.stages[si]
        start = 0
        if k > 0 and self.machine_stage[self.order[k - 1]] == si:
            start = placed[-1][5] + 1  # type: ignore[misc]
        cands = self.anchors[si]
        for ai in range(start, len(cands)):
            x, y, mask, slots = cands[ai]
            self.stats.nodes["machine"] += 1
            if mask & occ:
                self.stats.pruned["overlap"] += 1
                continue
            if not sf.final:
                # a producer must reach some placed consumer of its product
                ok = False
                for pm, px, py, pmask, _, _ in placed:  # type: ignore[misc]
                    if sf.product in self.flows.stages[self.machine_stage[pm]].internal_in:
                        for idx, _ in self.links((x, y, mask), (px, py, pmask)):
                            if not ((occ | mask) >> idx) & 1:
                                ok = True
                                break
                    if ok:
                        break
                if not ok:
                    self.stats.pruned["dangling_producer"] += 1
                    continue
            yield from self.place_machines(
                k + 1,
                [*placed, (m, x, y, mask, slots, ai)],
                occ | mask,  # type: ignore[list-item]
            )

    def full_placement(self, placed: list[Any], occ: int) -> Iterator[Period]:
        self.stats.nodes["machine_leaf"] += 1
        occ_all = occ | self.belt_mask
        reqs: list[tuple[str, int, int, list[_Slot], int]] = []  # kind, machine, other, slots, k
        for m, x, y, _mask, _, _ in placed:
            si = self.machine_stage[m]
            sf = self.flows.stages[si]
            free = [s for s in self.belt_slots(si, x, y) if not (occ_all >> s.tile) & 1]
            per_belt: dict[int, float] = {}
            for item, rate in sf.external_in.items():
                b = self.belt_of[("input", item)]  # type: ignore[index]
                per_belt[b] = per_belt.get(b, 0.0) + rate
            for b, rate in sorted(per_belt.items()):
                ss = [
                    _Slot(s.tile, s.direction, "in", m, b)
                    for s in free
                    if s.kind == "in" and s.other == b
                ]
                reqs.append(("in", m, b, ss, ceil_div(rate, self.cap)))
            if sf.final:
                b = self.belt_of[("output", sf.product)]  # type: ignore[index]
                ss = [
                    _Slot(s.tile, s.direction, "out", m, b, s.lane)
                    for s in free
                    if s.kind == "out" and s.other == b
                ]
                reqs.append(("out", m, b, ss, ceil_div(sf.rate, self.cap)))
        # links per consumer
        link_reqs: list[tuple[str, int, int, list[_Slot], int]] = []
        caps: dict[str, dict[tuple[str, str], float]] = {}
        for c, cx, cy, cmask, _, _ in placed:
            csf = self.flows.stages[self.machine_stage[c]]
            for item, rate in csf.internal_in.items():
                ss: list[_Slot] = []
                for p, px, py, pmask, _, _ in placed:
                    if self.flows.stages[self.machine_stage[p]].product != item:
                        continue
                    pl = [
                        _Slot(idx, d, "link", c, p)
                        for idx, d in self.links((px, py, pmask), (cx, cy, cmask))
                        if not (occ_all >> idx) & 1
                    ]
                    ss += pl
                    n_tiles = len({s.tile for s in pl})
                    if n_tiles:
                        g = caps.setdefault(item, {})
                        g[("s", f"p{p}")] = self.flows.stages[self.machine_stage[p]].rate
                        g[(f"p{p}", f"c{c}")] = n_tiles * self.cap
                g = caps.setdefault(item, {})
                g[(f"c{c}", "t")] = rate
                link_reqs.append(("link", c, -1, ss, ceil_div(rate, self.cap)))
        for kind, _m, _o, ss, k in reqs + link_reqs:
            if len({s.tile for s in ss}) < k:
                self.stats.pruned[f"slots_{kind}"] += 1
                return
        for g in caps.values():
            demand = sum(v for (a, b), v in g.items() if b == "t")
            if _maxflow(g, "s", "t") < demand - _EPS:
                self.stats.pruned["flow_upper_bound"] += 1
                return
        self.link_ub = caps
        free_tiles = self.w * self.h - bin(occ_all).count("1")
        if free_tiles < self.min_inserters + 1:
            self.stats.pruned["tile_budget"] += 1
            return
        reqs.sort(key=lambda r: len(r[3]) - r[4])
        link_reqs.sort(key=lambda r: len(r[3]) - r[4])
        # slots nearest the machine's centre first (they leave the corners to poles)
        cen = {
            m: (
                x + self.msize[self.machine_stage[m]][0] / 2,
                y + self.msize[self.machine_stage[m]][1] / 2,
            )
            for m, x, y, _mask, _, _ in placed
        }

        def dist(s: _Slot) -> float:
            cx, cy = cen[s.machine]
            return abs(s.tile % self.w + 0.5 - cx) + abs(s.tile // self.w + 0.5 - cy)

        for r in reqs + link_reqs:
            r[3].sort(key=dist)
        per = self.poles_first(placed, occ_all, reqs, link_reqs)
        if per is not None:
            yield per

    # ---- inserters ---------------------------------------------------------------------
    def choose_inserters(
        self,
        reqs: list[tuple[str, int, int, list[_Slot], int]],
        k: int,
        chosen: list[_Slot],
        used: int,
        budget: list[int] | None = None,
    ) -> Iterator[tuple[list[_Slot], int]]:
        """Every assignment of slots to the requirements ``reqs[k:]`` (fewest inserters
        per requirement first; links may take up to two extra)."""
        self.check_time()
        if k == len(reqs):
            yield chosen, used
            return
        kind, _m, _o, slots, need = reqs[k]
        avail = [s for s in slots if not (used >> s.tile) & 1]
        sizes = [need] if kind != "link" else [need, need + 1, need + 2]
        any_ok = False
        for n in sizes:
            for combo in itertools.combinations(avail, n):
                tiles = {s.tile for s in combo}
                if len(tiles) < n:
                    continue
                self.stats.nodes["inserter"] += 1
                if budget is not None:
                    budget[0] -= 1
                    if budget[0] < 0:
                        raise _InserterBudget
                nxt = [*chosen, *combo]
                if kind == "link" and not self.flow_ok(nxt, reqs[: k + 1]):
                    self.stats.pruned["flow"] += 1
                    continue
                any_ok = True
                u = used
                for t in tiles:
                    u |= 1 << t
                yield from self.choose_inserters(reqs, k + 1, nxt, u, budget)
        if not any_ok:
            self.stats.pruned["inserters_exhausted"] += 1

    def flow_ok(self, chosen: list[_Slot], done: list[Any]) -> bool:
        """Max-flow of the intermediate over the links chosen for the consumers decided
        so far plus every candidate link (``self.link_ub``) of the others."""
        decided = {r[1] for r in done if r[0] == "link"}
        by_item: dict[str, dict[tuple[str, str], float]] = {}
        for item, g0 in self.link_ub.items():
            g = by_item.setdefault(item, {})
            for (a, b), v in g0.items():
                if b == "t" or a == "s" or int(b[1:]) not in decided:
                    g[(a, b)] = v
        for s in chosen:
            if s.kind != "link":
                continue
            psf = self.flows.stages[self.machine_stage[s.other]]
            g = by_item[psf.product]
            e = (f"p{s.other}", f"c{s.machine}")
            g[e] = g.get(e, 0.0) + self.cap
        for g in by_item.values():
            demand = sum(v for (a, b), v in g.items() if b == "t")
            if _maxflow(g, "s", "t") < demand - _EPS:
                return False
        return True

    def output_lanes_ok(self, chosen: list[_Slot]) -> bool:
        load: dict[tuple[int, str], float] = {}
        for m in {s.machine for s in chosen if s.kind == "out"}:
            outs = [s for s in chosen if s.kind == "out" and s.machine == m]
            rate = self.flows.stages[self.machine_stage[m]].rate
            for s in outs:
                key = (s.other, str(s.lane))
                load[key] = load.get(key, 0.0) + rate / len(outs)
        return all(v * self.repeats <= self.lane_cap + _EPS for v in load.values())

    # ---- poles, then inserters on the tiles they supply --------------------------------
    def poles_first(
        self,
        placed: list[Any],
        occ: int,
        reqs: list[tuple[str, int, int, list[_Slot], int]],
        link_reqs: list[tuple[str, int, int, list[_Slot], int]],
    ) -> Period | None:
        """Fewest poles first (iterative deepening from a wire lower bound): a pole set
        must supply every machine and be one network that reaches the next period; then
        the inserters are chosen among the free slots it supplies (an inserter is a
        consumer too). Poles only for inserters are added when no inserter assignment
        fits the set."""
        w, h = self.w, self.h
        sx0, sy0, sx1, sy1 = self.supply
        boxes: list[tuple[int, int, int, int]] = []
        for m, x, y, _mask, _, _ in placed:
            if self.gd.entity(self.spec.stages[self.machine_stage[m]].machine).is_electric:
                mw, mh = self.msize[self.machine_stage[m]]
                boxes.append((x, y, x + mw - 1, y + mh - 1))
        slot_tiles = 0
        for r in reqs + link_reqs:
            for sl in r[3]:
                slot_tiles |= 1 << sl.tile
        cands: list[tuple[int, int, int, float, float]] = []  # x, y, own mask, cx, cy
        cover: list[int] = []  # machines supplied (bits over boxes)
        supply: list[int] = []  # tiles supplied (bits over the period)
        for y in range(h - self.ph + 1):
            for x in range(w - self.pw + 1):
                mask = 0
                for i in range(self.pw):
                    for j in range(self.ph):
                        mask |= 1 << ((y + j) * w + x + i)
                if mask & occ:
                    continue
                ax0, ay0 = max(0, x + sx0), max(0, y + sy0)
                ax1, ay1 = min(w - 1, x + sx1), min(h - 1, y + sy1)
                bits = 0
                for ci, (cx0, cy0, cx1, cy1) in enumerate(boxes):
                    if cx0 <= ax1 and cx1 >= ax0 and cy0 <= ay1 and cy1 >= ay0:
                        bits |= 1 << ci
                sup = 0
                row = ((1 << (ax1 - ax0 + 1)) - 1) << ax0
                for yy in range(ay0, ay1 + 1):
                    sup |= row << (yy * w)
                cands.append((x, y, mask, x + self.pole_c[0], y + self.pole_c[1]))
                cover.append(bits)
                supply.append(sup)
        n = len(cands)
        self.stats.variables["pole_candidates_max"] = max(
            self.stats.variables.get("pole_candidates_max", 0), n
        )
        full = (1 << len(boxes)) - 1
        d2 = self.wire2
        reach = [
            [
                (cands[a][3] - cands[b][3]) ** 2 + (cands[a][4] - cands[b][4]) ** 2 <= d2
                for b in range(n)
            ]
            for a in range(n)
        ]
        cross = [
            [
                (cands[a][3] - cands[b][3] - w) ** 2 + (cands[a][4] - cands[b][4]) ** 2 <= d2
                for b in range(n)
            ]
            for a in range(n)
        ]
        by_box = [[a for a in range(n) if cover[a] >> ci & 1] for ci in range(len(boxes))]
        if any(not lst for lst in by_box):
            self.stats.pruned["power_cover"] += 1
            return None
        best_cover = max((bin(c).count("1") for c in cover), default=0)

        def connected(sel: list[int]) -> bool:
            seen, stack = {sel[0]}, [sel[0]]
            while stack:
                a = stack.pop()
                for b in sel:
                    if b not in seen and reach[a][b]:
                        seen.add(b)
                        stack.append(b)
            return len(seen) == len(sel)

        budget = [self.pole_budget]

        def inserters_for(sel: list[int], pmask: int) -> Period | None:
            sup = 0
            for a in sel:
                sup |= supply[a]
            ok_tiles = sup & ~pmask
            rr = []
            for kind, m, o, slots, k in link_reqs + reqs:
                ss = [sl for sl in slots if ok_tiles >> sl.tile & 1]
                if len({sl.tile for sl in ss}) < k:
                    self.stats.pruned[f"power_slots_{kind}"] += 1
                    return None
                rr.append((kind, m, o, ss, k))
            ins_budget = [self.inserter_budget]
            try:
                for chosen, _used in self.choose_inserters(rr, 0, [], 0, ins_budget):
                    if not self.output_lanes_ok(chosen):
                        self.stats.pruned["lane_capacity"] += 1
                        continue
                    self.stats.nodes["inserter_leaf"] += 1
                    return self.build(chosen, placed, [cands[a] for a in sel])
            except _InserterBudget:
                self.stats.pruned["inserter_budget"] += 1
            return None

        def dfs(
            sel: list[int], covered: int, pmask: int, limit: int, ext_from: int
        ) -> Period | None:
            budget[0] -= 1
            if budget[0] < 0:
                raise _PoleBudget
            self.stats.nodes["pole"] += 1
            if covered == full:
                linked = connected(sel) and any(cross[a][b] for a in sel for b in sel)
                if linked and len(sel) == limit:
                    return inserters_for(sel, pmask)
                if len(sel) >= limit:
                    self.stats.pruned["power_wire"] += 1
                    return None
                if linked:
                    # one more pole for inserters the set does not supply yet
                    sup = 0
                    for a in sel:
                        sup |= supply[a]
                    lacking = slot_tiles & ~sup
                    opts = range(ext_from, n)
                    for a in opts:
                        if a in sel or cands[a][2] & pmask or not supply[a] & lacking:
                            continue
                        if not any(reach[a][b] for b in sel):
                            continue
                        r = dfs([*sel, a], covered, pmask | cands[a][2], limit, a + 1)
                        if r:
                            return r
                    return None
                for a in range(n):
                    if a in sel or cands[a][2] & pmask or not any(reach[a][b] for b in sel):
                        continue
                    r = dfs([*sel, a], covered, pmask | cands[a][2], limit, ext_from)
                    if r:
                        return r
                return None
            left = limit - len(sel)
            missing = bin(full & ~covered).count("1")
            if left <= 0 or left * best_cover < missing:
                self.stats.pruned["power_cover"] += 1
                return None
            ci = min(
                (c for c in range(len(boxes)) if not covered >> c & 1), key=lambda c: len(by_box[c])
            )
            opts2 = [a for a in by_box[ci] if not cands[a][2] & pmask]
            opts2.sort(key=lambda a: -bin(cover[a] & ~covered).count("1"))
            for a in opts2:
                r = dfs([*sel, a], covered | cover[a], pmask | cands[a][2], limit, ext_from)
                if r:
                    return r
            return None

        lower = max(1, math.ceil(w / math.sqrt(d2) - _EPS))  # copies need a pole per reach
        try:
            for limit in range(lower, len(boxes) + self.min_inserters + 1):
                self.check_time()
                r = dfs([], 0, 0, limit, 0)
                if r is not None:
                    return r
        except _PoleBudget:
            self.stats.pruned["pole_budget"] += 1
        return None

    # ---- result ------------------------------------------------------------------------
    def build(self, chosen: list[_Slot], placed: list[Any], poles: list[Any]) -> Period:
        gd, spec, w = self.gd, self.spec, self.w
        ps: list[Placement] = []
        stage_of: list[int | None] = []
        for y in self.ys:
            for x in range(w):
                ps.append(Placement(spec.belt, center(gd, spec.belt, x, y, FLOW), FLOW))
                stage_of.append(None)
        for m, x, y, _mask, _, _ in sorted(placed, key=lambda p: p[0]):
            st = spec.stages[self.machine_stage[m]]
            ps.append(
                Placement(st.machine, center(gd, st.machine, x, y), extra={"recipe": st.recipe})
            )
            stage_of.append(self.machine_stage[m])
        for s in sorted(chosen, key=lambda s: s.tile):
            tx, ty = s.tile % w, s.tile // w
            ps.append(Placement(spec.inserter, center(gd, spec.inserter, tx, ty), s.direction))
            stage_of.append(None)
        for x, y, _mask, _cx, _cy in sorted(poles, key=lambda q: (q[0], q[1])):
            ps.append(Placement(spec.pole, center(gd, spec.pole, x, y)))
            stage_of.append(None)
        rows: list[BeltRow] = []
        for b, y in zip(self.belts, self.ys, strict=True):
            if b.role == "input":
                if len(b.materials) == 1:
                    lanes: tuple[tuple[Any, str], ...] = (
                        ("left", b.materials[0]),
                        ("right", b.materials[0]),
                    )
                    r = self.flows.input_rate[b.materials[0]] / 2
                    rates: tuple[tuple[Any, float], ...] = (("left", r), ("right", r))
                else:
                    lanes = tuple(zip(("left", "right"), b.materials, strict=False))
                    rates = tuple((ln, self.flows.input_rate[mat]) for ln, mat in lanes)
                rows.append(BeltRow(y, "input", lanes, rates))
            else:
                load: dict[str, float] = {}
                k = self.belts.index(b)
                for m in {s.machine for s in chosen if s.kind == "out" and s.other == k}:
                    outs = [s for s in chosen if s.kind == "out" and s.machine == m]
                    rate = self.flows.stages[self.machine_stage[m]].rate
                    for s in outs:
                        load[str(s.lane)] = load.get(str(s.lane), 0.0) + rate / len(outs)
                rows.append(
                    BeltRow(
                        y,
                        "output",
                        tuple((ln, b.materials[0]) for ln in sorted(load)),  # type: ignore[misc]
                        tuple(sorted(load.items())),  # type: ignore[arg-type]
                    )
                )
        links = Counter(
            (s.other, s.machine) for s in chosen if s.kind == "link"
        )  # (producer, consumer) -> inserters
        meta = {
            "links": sorted([p, c, n] for (p, c), n in links.items()),
            "inserters": len(chosen),
            "poles": len(poles),
        }
        return Period(w, self.h, tuple(ps), tuple(rows), spec, tuple(stage_of), meta)


class _PoleBudget(Exception):
    pass


class _InserterBudget(Exception):
    pass


def _belt_key(k: tuple[tuple[_Belt, ...], tuple[int, ...]]) -> tuple[Any, ...]:
    return (tuple((b.role, b.materials) for b in k[0]), k[1])


def solve(
    spec: CycleSpec,
    gd: GameData,
    *,
    repeats: int = 1,
    max_width: int = 32,
    max_height: int = 16,
    time_limit: float = 60.0,
    candidates: int = 3,
    inserter_budget: int = 20_000,
    pole_budget: int = 200_000,
    area_slack: float = 0.0,
) -> SolveResult:
    """Smallest periods for ``spec`` (up to ``candidates`` of the smallest feasible
    area, fewest entities first). ``repeats``: how many periods will share each belt
    lane (lane capacity is checked for that many)."""
    t0 = time.monotonic()
    stats = SolveStats()
    s = _Search(spec, gd, repeats, t0 + time_limit, stats, inserter_budget, pole_budget)
    stats.variables.update(
        machines=spec.machines(),
        min_inserters=s.min_inserters,
        inserter_cap_milli=round(s.cap * 1000),
    )
    mw = max(w for w, _ in s.msize.values())
    mh = max(h for _, h in s.msize.values())
    sizes = sorted(
        ((w, h) for w in range(mw, max_width + 1) for h in range(mh + 1, max_height + 1)),
        key=lambda p: (p[0] * p[1], p[1]),
    )
    found: list[Period] = []
    best_area: int | None = None
    try:
        for w, h in sizes:
            if best_area is not None and w * h > best_area * (1 + area_slack) + _EPS:
                break
            if s.machine_tiles() + s.min_inserters + 1 + w > w * h:
                stats.pruned["area_bound"] += 1
                continue
            ts = time.monotonic()
            before = sum(stats.nodes.values())
            n_found = 0
            for per in s.size(w, h):
                per.meta["seconds_to_find"] = round(time.monotonic() - t0, 3)
                found.append(per)
                n_found += 1
                best_area = best_area or w * h
                if len(found) >= candidates:
                    break
            stats.sizes.append(
                {
                    "width": w,
                    "height": h,
                    "seconds": round(time.monotonic() - ts, 3),
                    "nodes": sum(stats.nodes.values()) - before,
                    "found": n_found,
                }
            )
            if len(found) >= candidates:
                break
    except _Timeout:
        stats.timed_out = True
    stats.seconds = time.monotonic() - t0
    found.sort(key=lambda p: (p.area, len(p.placements)))
    return SolveResult(found, stats)
