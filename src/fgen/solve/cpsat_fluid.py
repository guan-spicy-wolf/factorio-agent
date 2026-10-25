"""CP-SAT vocabulary for fluids in a period (plan-phase5a §2.3).

Added to :func:`fgen.solve.cpsat._solve_height` when a stage's recipe has fluid
ingredients or products. The structure is a **pipe network inside the period**, not a
fixed bus row: the solver places plain pipes and pipe-to-ground ends on free tiles and
turns the fluid machines; a full pipe row along the belts is one of its solutions.

Variables (boolean):

- ``pipe[t, f]``: a plain pipe of fluid ``f`` on tile ``t`` (connects on all four sides);
- ``ptg[t, d, f]``: a pipe-to-ground of ``f`` on ``t`` whose normal connection faces ``d``
  (its underground side faces ``-d``); ``pair[...]``: two ends that pair (same fluid,
  facing away from each other, at most ``max_underground_distance`` apart, no
  same-axis pipe-to-ground between them: the game pairs the nearest, VERIFY §13);
- ``pose[s, a, k]``: the machine of stage ``s`` at anchor ``a`` faces direction ``k``
  (``sum_k pose = P[s, a]``); the ports of each pose come from
  :func:`fgen.geom.fluid.machine_ports` (mirror first, then rotate; one recipe fluid
  fills every box of its kind; unused boxes idle). Mirroring is the spec's parameter;
- ``cross[f, y]``: ``f`` crosses the period cut on row ``y``: plain pipes on ``(0, y)``
  and ``(W - 1, y)`` (exactly one row per fluid; the unit's port).

Constraints:

- every recipe fluid box of a placed machine has a port whose outside tile holds a
  connection of its fluid facing it;
- **no unintended adjacency**: on every pair of neighbouring tiles (and across the cut
  in a fixed-width model) two connections facing each other carry the same fluid; an
  idle machine port faces no connection at all (conservative: idle ports do not connect
  in 2.1.23, but a recipe can be changed); no machine port faces across the cut;
- every pipe-to-ground end is paired (fixed width; the screen only requires
  ``pairs <= ends``, a relaxation);
- **one network per fluid inside the period** (fixed width): a single-commodity flow
  from the crossing tile ``(0, y)`` reaches every pipe of the fluid over connections
  that face each other and over pairs; with the crossing that makes the copies one
  network (as for the poles: a period's own network plus one link to the next).

Fluid rates are not constrained: inside a segment the flow does not depend on length
(VERIFY §13); the unit's segment extent is checked by the generator.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fgen.data.schema import GameData
from fgen.geom import fluid
from fgen.geom.vec import CARDINALS, Direction
from fgen.solve.period import CycleFlows, CycleSpec, center

E, W, N, S = Direction.E, Direction.W, Direction.N, Direction.S
type T = tuple[int, int]


class FluidCtx:
    """Spec-level numbers: fluids, machine poses with their ports, underground reach."""

    def __init__(self, spec: CycleSpec, gd: GameData, f: CycleFlows) -> None:
        self.fluids = [*f.fluid_inputs, *f.fluid_outputs]
        self.poses: dict[int, list[tuple[Direction, list[fluid.Port]]]] = {}
        for si, st in enumerate(spec.stages):
            ins, outs = fluid.recipe_fluids(gd, st.recipe)
            if not ins and not outs:
                continue
            if not set(ins + outs) <= set(self.fluids):
                raise ValueError(f"{st.recipe}: a fluid made and used inside the period")
            e = gd.entity(st.machine)
            if not e.fluid_boxes:
                raise ValueError(f"{st.machine} has no fluid boxes for {st.recipe}")
            mirror = spec.mirror and e.use_mirroring
            out = []
            for d in CARDINALS:
                c = center(gd, st.machine, 0, 0, d)
                out.append((d, fluid.machine_ports(gd, st.machine, c, d, st.recipe, mirror)))
            self.poses[si] = out
        ptg = spec.pipe_to_ground
        self.maxd = int(gd.entity(ptg).max_underground_distance or 0) if ptg else 0


@dataclass
class FluidVars:
    pipe: dict[tuple[T, str], Any] = field(default_factory=dict)
    ptg: dict[tuple[T, Direction, str], Any] = field(default_factory=dict)
    pose: dict[tuple[int, T, int], Any] = field(default_factory=dict)
    cross: dict[str, list[Any]] = field(default_factory=dict)
    count: Any = 0


def add(
    m: Any,
    fc: FluidCtx,
    h: int,
    w: int,
    u: list[Any],
    fixed: bool,
    occ: dict[T, list[Any]],
    P: list[dict[T, Any]],
) -> FluidVars:
    """Fluid variables and constraints for an ``h`` x ``w`` model (columns ``u``;
    ``fixed``: the width is exactly ``w``, so the cut wraps ``x = w - 1`` to ``x = 0``)."""
    fv = FluidVars()
    tiles = [(x, y) for y in range(h) for x in range(w)]
    dirs = (E, W, N, S) if fc.maxd else ()
    for t in tiles:
        for f in fc.fluids:
            v = m.new_bool_var("")
            m.add(v <= u[t[0]])
            fv.pipe[(t, f)] = v
            occ[t].append(v)
            for d in dirs:
                g = m.new_bool_var("")
                m.add(g <= u[t[0]])
                fv.ptg[(t, d, f)] = g
                occ[t].append(g)
    # connections facing a neighbour: (tile, direction) -> label -> literals
    conn: dict[tuple[T, Direction], dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for (t, f), v in fv.pipe.items():
        for d in CARDINALS:
            conn[(t, d)][f].append(v)
    for (t, d, f), g in fv.ptg.items():
        conn[(t, d)][f].append(g)

    def facing(t: T, d: Direction, f: str) -> list[Any]:
        """Connections of ``f`` on ``t`` that face direction ``d``."""
        out = [fv.pipe[(t, f)]]
        if fc.maxd:
            out.append(fv.ptg[(t, d, f)])
        return out

    # machine poses and their ports
    for si, poses in fc.poses.items():
        for a, pv in P[si].items():
            lits = []
            for k, (_, pts) in enumerate(poses):
                v = m.new_bool_var("")
                lits.append(v)
                fv.pose[(si, a, k)] = v
                groups: dict[tuple[str, str], list[tuple[T, Direction]]] = defaultdict(list)
                ok = True
                for pt in pts:
                    tt = (a[0] + pt.tile.x, a[1] + pt.tile.y)
                    o = (a[0] + pt.outside.x, a[1] + pt.outside.y)
                    if not 0 <= o[0] < w:
                        ok = False  # no machine port faces across the cut
                        break
                    m.add(v <= u[o[0]])
                    conn[(tt, pt.direction)][pt.fluid or fluid.IDLE].append(v)
                    if pt.fluid is not None:
                        groups[(pt.role, pt.fluid)].append((o, pt.direction.opposite))
                if not ok:
                    m.add(v == 0)
                    continue
                for (_, f), outs in groups.items():
                    att = [lit for o, back in outs if 0 <= o[1] < h for lit in facing(o, back, f)]
                    if not att:
                        m.add(v == 0)
                    else:
                        m.add(sum(att) >= v)
            m.add(sum(lits) == pv)

    # no unintended adjacency (labels: fluids, or IDLE for an idle machine port)
    inner = [((x, y), (x + 1, y), E) for x, y in tiles if x + 1 < w]
    inner += [((x, y), (x, y + 1), S) for x, y in tiles if y + 1 < h]
    wrap = [((w - 1, y), (0, y), E) for y in range(h)] if fixed else []
    for t, n, d in inner + wrap:
        a_, b_ = conn.get((t, d)), conn.get((n, d.opposite))
        if not a_ or not b_:
            continue
        for la, va in a_.items():
            for lb, vb in b_.items():
                if la != lb:
                    m.add(sum(va) + sum(vb) <= 1)

    # the crossing of each fluid
    for f in fc.fluids:
        row = [m.new_bool_var("") for _ in range(h)]
        m.add_exactly_one(row)
        for y, c in enumerate(row):
            m.add(c <= fv.pipe[((0, y), f)])
            if fixed:
                m.add(c <= fv.pipe[((w - 1, y), f)])
        fv.cross[f] = row

    # pipe-to-ground pairs: (end a, end b, intra-period?)
    pair_edges: dict[str, list[tuple[T, T, Any]]] = defaultdict(list)
    spans: dict[tuple[str, int], list[Any]] = defaultdict(list)  # (fluid, column) -> pairs over it
    if fc.maxd:
        ends: dict[tuple[T, Direction, str], list[Any]] = defaultdict(list)
        for f in fc.fluids:
            for y in range(h):
                for a in range(w):
                    for b in range(a + 1, min(w, a + fc.maxd + 1)):
                        mid = [(x, y) for x in range(a + 1, b)]
                        v = _pair(m, fv, f, (a, y), W, (b, y), E, mid, fc.fluids, (E, W))
                        ends[((a, y), W, f)].append(v)
                        ends[((b, y), E, f)].append(v)
                        pair_edges[f].append(((a, y), (b, y), v))
                        for x in range(a + 1, b):
                            spans[(f, x)].append(v)
                    if fixed:  # across the cut: a near the right edge, b in the next copy
                        for b in range(0, min(w, a + fc.maxd + 1 - w)):
                            mid = [(x, y) for x in range(a + 1, w)] + [(x, y) for x in range(b)]
                            v = _pair(m, fv, f, (a, y), W, (b, y), E, mid, fc.fluids, (E, W))
                            ends[((a, y), W, f)].append(v)
                            ends[((b, y), E, f)].append(v)
                            for x in [*range(a + 1, w), *range(b)]:
                                spans[(f, x)].append(v)
            for x in range(w):
                for a in range(h):
                    for b in range(a + 1, min(h, a + fc.maxd + 1)):
                        mid = [(x, y) for y in range(a + 1, b)]
                        v = _pair(m, fv, f, (x, a), N, (x, b), S, mid, fc.fluids, (N, S))
                        ends[((x, a), N, f)].append(v)
                        ends[((x, b), S, f)].append(v)
                        pair_edges[f].append(((x, a), (x, b), v))
        for key, g in fv.ptg.items():
            ps = ends.get(key, [])
            if fixed:
                m.add(g == sum(ps))
            else:
                m.add(g >= sum(ps))

    # a cut that holds for any width: a fluid's network runs from x = 0 to x = W - 1, so
    # every column holds one of its entities or lies under one of its underground spans
    # (without a fixed width, an entrance anywhere left of the column may span the cut)
    for f in fc.fluids:
        for x in range(w):
            here = [fv.pipe[((x, y), f)] for y in range(h)]
            here += [fv.ptg[((x, y), d, f)] for y in range(h) for d in dirs]
            here += spans[(f, x)]
            if fc.maxd and not fixed:
                here += [fv.ptg[((a, y), W, f)] for a in range(x) for y in range(h)]
            m.add(sum(here) >= u[x])

    # one network per fluid inside the period: flow from the crossing tile (0, y)
    if fixed:
        big = len(tiles)
        for f in fc.fluids:
            node = {t: [fv.pipe[(t, f)], *(fv.ptg[(t, d, f)] for d in dirs)] for t in tiles}
            inflow: dict[T, list[Any]] = defaultdict(list)
            outflow: dict[T, list[Any]] = defaultdict(list)
            arcs = []
            for t, n, d in inner:  # the wrap is the crossing, not the period's own network
                e = m.new_bool_var("")
                m.add(e <= sum(facing(t, d, f)))
                m.add(e <= sum(facing(n, d.opposite, f)))
                arcs.append((t, n, e))
            arcs += pair_edges[f]
            for t, n, e in arcs:
                for s_, d_ in ((t, n), (n, t)):
                    q = m.new_int_var(0, big, "")
                    m.add(q <= big * e)
                    outflow[s_].append(q)
                    inflow[d_].append(q)
            roots = []
            for y, c in enumerate(fv.cross[f]):
                r = m.new_int_var(0, big, "")
                m.add(r <= big * c)
                inflow[(0, y)].append(r)
                roots.append(r)
            for t in tiles:
                m.add(sum(inflow[t]) - sum(outflow[t]) == sum(node[t]))
    fv.count = sum(fv.pipe.values()) + sum(fv.ptg.values())
    return fv


def _pair(
    m: Any,
    fv: FluidVars,
    f: str,
    a: T,
    da: Direction,
    b: T,
    db: Direction,
    mid: list[T],
    fluids: list[str],
    axis: tuple[Direction, Direction],
) -> Any:
    v = m.new_bool_var("")
    m.add(v <= fv.ptg[(a, da, f)])
    m.add(v <= fv.ptg[(b, db, f)])
    between = [fv.ptg[(t, d, g)] for t in mid for d in axis for g in fluids]
    if between:
        m.add(sum(between) <= len(mid) * (1 - v))
    return v


def read(solver: Any, fc: FluidCtx, fv: FluidVars) -> tuple[list[Any], dict[str, int]]:
    """Pipes as ``(x, y, fluid, None | normal direction)`` and fluid -> crossing row."""
    pipes: list[Any] = [(t[0], t[1], f, None) for (t, f), v in fv.pipe.items() if solver.value(v)]
    pipes += [(t[0], t[1], f, d) for (t, d, f), v in fv.ptg.items() if solver.value(v)]
    cross = {
        f: next(y for y, c in enumerate(row) if solver.value(c)) for f, row in fv.cross.items()
    }
    return pipes, cross


def directions(
    solver: Any, fc: FluidCtx, fv: FluidVars, anchors: dict[tuple[int, T], int]
) -> dict[int, Direction]:
    """Machine index (``anchors``: (stage, anchor) -> index) -> chosen direction."""
    out = {}
    for (si, a, k), v in fv.pose.items():
        if solver.value(v) and (si, a) in anchors:
            out[anchors[(si, a)]] = fc.poses[si][k][0]
    return out
