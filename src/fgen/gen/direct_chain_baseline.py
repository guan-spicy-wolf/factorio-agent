"""Hand-written baseline for the direct-insertion chain (plan-phase4a §1.3).

The classic ratio row, written by hand as a template (not searched)::

    y=0  input belt  >>>>>>>>>>>>>>>>>>>   both raw materials, one per lane
    y=1  . i . . . i . . . i . . . i . . . i .   one inserter per machine (more if the
    y=2  W W W i C C C i W W W i C C C i W W W   rate needs), from the belt
    y=3  W W W P C C C P W W W P C C C P W W W   producer -> consumer inserters and
    y=4  W W W . C C C . W W W . C C C . W W W   poles in the 1-tile gap columns
    y=5  . . . . . i . . . . . . . i . . . . .   consumer -> output belt
    y=6  output belt >>>>>>>>>>>>>>>>>>>>>

Producers (W) and consumers (C) alternate, so it needs ``producers = consumers + 1``
(3:2, 2:1). Each consumer takes what it can from its left producer and the rest from
its right one; inserters per link, per input and per output are ``ceil(rate /
inserter estimate)``, stacked from the top of the gap column. A pole stands in every
gap column (the free tile nearest the middle row); poles in the inserter rows are added
(left to right) only while some consumer is still unsupplied. Row offsets come from the
inserter reach and the machine footprint, as in the smelting row. The result is a
:class:`~fgen.solve.period.Period`, checked like a solved one.
"""

from __future__ import annotations

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.geom import belt as beltgeom
from fgen.geom import footprint, inserter, pole
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Placement
from fgen.solve.period import FLOW, BeltRow, CycleSpec, Period, ceil_div, center, flows

FACING = Direction.N  # input/output inserters: pick up from the north, drop to the south


def baseline_period(spec: CycleSpec, gd: GameData) -> Period:
    f = flows(spec, gd)
    finals = [i for i, sf in enumerate(f.stages) if sf.final]
    if len(spec.stages) != 2 or len(finals) != 1:
        raise Infeasible("the baseline template handles one producer and one consumer stage")
    ci = finals[0]
    pi = 1 - ci
    cons, prod = spec.stages[ci], spec.stages[pi]
    if prod.count != cons.count + 1:
        raise Infeasible(
            f"the baseline template alternates machines: needs {cons.count + 1} producers "
            f"for {cons.count} consumers, got {prod.count}"
        )
    if len(f.inputs) > int(gd.constant("belt_lanes")):
        raise Infeasible("the baseline template carries every raw material on one belt")
    pu, dr = inserter.reach_offsets(gd, spec.inserter)
    if pu != Tile(0, -1) or dr != Tile(0, 1):
        raise Infeasible(f"the baseline template needs a 1-tile inserter, not {spec.inserter}")
    mw, mh = footprint.size(gd, cons.machine)
    if footprint.size(gd, prod.machine) != (mw, mh):
        raise Infeasible("the baseline template needs equal machine footprints")
    cap = tp.inserter_throughput_estimate(gd, spec.inserter)
    sc, sp = f.stages[ci], f.stages[pi]

    y_in, y_ins_in = 0, 1
    y_m = y_ins_in + dr.y
    y_ins_out = y_m + mh - 1 - pu.y
    y_out = y_ins_out + dr.y
    n = prod.count + cons.count
    width = n * mw + (n - 1)
    height = y_out + 1
    ps: list[Placement] = []
    stage_of: list[int | None] = []

    def put(
        name: str, x: int, y: int, d: Direction = Direction.N, stage: int | None = None, **extra
    ):
        ps.append(Placement(name, center(gd, name, x, y, d), d, dict(extra)))
        stage_of.append(stage)

    for y in (y_in, y_out):
        for x in range(width):
            put(spec.belt, x, y, FLOW)
    kinds = [pi if k % 2 == 0 else ci for k in range(n)]
    xs = [k * (mw + 1) for k in range(n)]
    for k, (x, s) in enumerate(zip(xs, kinds, strict=True)):
        del k
        st = spec.stages[s]
        put(st.machine, x, y_m, stage=s, recipe=st.recipe)
    used: set[Tile] = set()

    def columns(x0: int, count: int) -> list[int]:
        """``count`` columns of a machine at ``x0``, middle first."""
        mid = x0 + (mw - 1) // 2
        order = sorted(range(x0, x0 + mw), key=lambda x: (abs(x - mid), x))
        if count > len(order):
            raise Infeasible(f"{count} inserters do not fit along one {mw}-wide side")
        return order[:count]

    for x, s in zip(xs, kinds, strict=True):
        sf = f.stages[s]
        for col in columns(x, ceil_div(sum(sf.external_in.values()), cap)):
            put(spec.inserter, col, y_ins_in, FACING)
            used.add(Tile(col, y_ins_in))
        if sf.final:
            for col in columns(x, ceil_div(sf.rate, cap)):
                put(spec.inserter, col, y_ins_out, FACING)
                used.add(Tile(col, y_ins_out))
    # links: consumer k takes from its left producer first, then from the right one
    supply = {k: sp.rate for k in range(n) if kinds[k] == pi}
    item = sp.product
    links: list[list[int]] = []
    for k in range(n):
        if kinds[k] != ci:
            continue
        need = sc.internal_in[item]
        for side, gap_x, d in ((k - 1, xs[k] - 1, Direction.W), (k + 1, xs[k] + mw, Direction.E)):
            take = min(need, supply[side])
            if take <= 0:
                continue
            supply[side] -= take
            need -= take
            count = ceil_div(take, cap)
            if count > mh:
                raise Infeasible(f"{count} inserters do not fit in a {mh}-tall gap column")
            for j in range(count):
                put(spec.inserter, gap_x, y_m + j, d)
                used.add(Tile(gap_x, y_m + j))
            links.append([side, k, count])
        if need > 1e-9:
            raise Infeasible("the alternating row cannot feed every consumer")
    # poles: one per gap column, nearest the middle row
    poles: list[Tile] = []
    mid = y_m + (mh - 1) // 2
    for k in range(n - 1):
        gx = xs[k] + mw
        free = sorted(
            (y for y in range(y_m, y_m + mh) if Tile(gx, y) not in used), key=lambda y: abs(y - mid)
        )
        if free:
            poles.append(Tile(gx, free[0]))
    consumers = [p for p in ps if gd.entity(p.name).is_electric]

    def unsupplied() -> list[Placement]:
        sup: set[Tile] = set()
        for t in poles:
            sup |= pole.supply_tiles(gd, spec.pole, center(gd, spec.pole, t.x, t.y))
        return [p for p in consumers if not (p.tiles(gd) & sup)]

    while True:
        left = unsupplied()
        if not left:
            break
        target = min(left, key=lambda q: (q.position.x, q.position.y))
        x = target.position.floor().x
        # a free tile of an inserter row whose supply area reaches the consumer
        spots = [
            Tile(sx, row)
            for row in (y_ins_in, y_ins_out)
            for sx in range(width)
            if Tile(sx, row) not in used
            and Tile(sx, row) not in poles
            and pole.supply_tiles(gd, spec.pole, center(gd, spec.pole, sx, row)) & target.tiles(gd)
        ]
        if not spots:
            raise Infeasible("no free tile for a pole next to an unsupplied consumer")
        # rightmost spot that still reaches it (covers the most of what follows)
        poles.append(max(spots, key=lambda t: (t.x, -abs(t.x - x))))
    for t in sorted(poles):
        put(spec.pole, t.x, t.y)
    out_lane = beltgeom.drop_lane(FLOW, FACING.opposite)
    ins = tuple(zip(("left", "right"), f.inputs, strict=False))
    if len(f.inputs) == 1:
        ins = (("left", f.inputs[0]), ("right", f.inputs[0]))
    in_rates = tuple((ln, f.input_rate[m] / (2 if len(f.inputs) == 1 else 1)) for ln, m in ins)
    belts = (
        BeltRow(y_in, "input", ins, in_rates),  # type: ignore[arg-type]
        BeltRow(
            y_out,
            "output",
            ((out_lane, sc.product),),
            ((out_lane, f.output_rate[sc.product]),),
        ),
    )
    meta = {
        "links": sorted(links),
        "inserters": sum(1 for p in ps if p.name == spec.inserter),
        "poles": len(poles),
        "baseline": True,
    }
    return Period(width, height, tuple(ps), belts, spec, tuple(stage_of), meta)
