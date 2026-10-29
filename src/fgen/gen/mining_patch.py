"""Generator family 2: drills over an ore patch (anchor unit, plan-phase2 §3.2).

Canonical frame (``axis="x"``, ``flow="+"``): belts run east along rows; each belt row
has a row of drills on each side facing it (north side faces S, south side faces N), so
every drill drops onto the belt (the drop tile comes from ``vector_to_place_result``,
the lane from where the drop point lies inside the belt tile: the drill's own side).
With the electric drill (pole ``P`` in a one-tile gap between the drills of its row)::

    DDDDDD.DDDDDDP.    north drill row (3 tall); a pole gap every ``s`` columns
    DDDDDD.DDDDDD..
    DDDDDD.DDDDDD..
    >>>>>>>>>>>>>>>    belt row: flows east; its east end is an output interface
    DDDDDDDDDDDDDDD
    DDDDDDDDDDDDDDD    south drill row (no gaps when only the north row has poles)
    DDDDDDDDDDDDDDD

Belt rows repeat every ``period`` = drill rows + belt + drill rows (7 for the 3x3
drill). Other axes/flows are the canonical frame rotated (``layout.transform``).

Derived from ``GameData`` (no entity names in the logic):

- drill positions: drop tile and footprint from the prototype; a drill gets a place when
  its search area (``fgen.geom.mining.search_tiles``) holds at least
  ``min_ore_tiles_per_drill`` patch tiles and ``min_ore_amount_per_drill`` ore, no other
  resource the drill would mine too (unless ``allow_mixed``; reasons ``low_ore`` /
  ``mixed_resource``), and its footprint and belt tile are buildable;
- the x/y phase of the grid is searched (``period`` x ``s`` options) for the most drills;
- poles: the in-row offset whose supply area reaches the drill rows of both sides
  (else one pole row per side). A pole needs only its own footprint, so it takes a gap of
  its width between drills instead of a drill slot: the gap spacing ``s`` is the one within
  ``maximum_wire_distance`` that fits the most drills per column; the same gap in every belt
  row so rows connect. A drill whose footprint no pole's supply area covers is dropped
  (reason ``pole_slot``, which should stay rare);
- lane capacity: each lane carries the drills of one side; drills beyond
  ``lane capacity / drill rate`` are cut from the upstream end (reason
  ``lane_capacity``) and ``meta["suggestions"]`` names a belt tier or axis that keeps them.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.geom import footprint, mining, pole
from fgen.geom.belt import Lane
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout import transform as tf
from fgen.layout.model import Interface, Layout, Placement, RateProfile, Unit
from fgen.layout.render import to_ascii
from fgen.layout.validate import Violation, validate_unit
from fgen.terrain.model import Area, Patch, Terrain, background, bounds_of

_EPS = 1e-9
FLOW = Direction.E
SIDES = ("north", "south")
FACING = {"north": Direction.S, "south": Direction.N}

# (axis, flow) -> rotation taking world into the canonical frame, and back
_TO_CANON = {
    ("x", "+"): None,
    ("x", "-"): Direction.S,
    ("y", "+"): Direction.W,
    ("y", "-"): Direction.E,
}
_INVERSE = {
    None: None,
    Direction.S: Direction.S,
    Direction.W: Direction.E,
    Direction.E: Direction.W,
}


@dataclass(frozen=True)
class MiningPatchParams:
    resource: str = "iron-ore"
    drill: str = "electric-mining-drill"
    belt: str = "transport-belt"
    pole: str = "small-electric-pole"
    axis: Literal["x", "y"] = "x"  # belt direction
    max_belts: int | None = None  # None: as many belt rows as the patch takes
    min_ore_tiles_per_drill: int = 1  # patch tiles needed in a drill's search area
    min_ore_amount_per_drill: int = 0  # ore (sum of amounts) needed in its search area
    allow_mixed: bool = False  # allow drills whose search area holds another resource
    flow: Literal["+", "-"] = "+"  # belts flow toward the + (E/S) or - (W/N) end of axis


@dataclass
class _Cand:
    row: int  # belt row index
    side: str
    k: int  # slot index along the belt
    x: int  # drop column (= belt tile x)
    pos: Vec2
    ore: int  # patch tiles in the search area
    lane: Lane


@dataclass
class _Geo:
    """Per-parameter geometry in the canonical frame (independent of the patch)."""

    w: int  # drill width along the belt
    period: int
    rows: dict[str, tuple[int, int]]  # side -> drill top-row offset from belt row, height
    anchor_dx: dict[str, int]  # side -> drill anchor x - drop column
    lanes: dict[str, Lane]
    pole_offsets: dict[str, list[tuple[int, int]]]  # side -> pole anchors to try (ox = 0)
    pole_sides: tuple[str, ...]  # sides that get poles; their drill rows have pole gaps
    pw: int  # pole width along the belt (the gap width)
    s: int  # pole spacing along the belt = gap period (in wire reach)


@dataclass
class _Result:
    placements: list[Placement]
    belts: list[dict[str, Any]]
    dropped: list[dict[str, Any]]
    drills: int
    poles: list[Placement]
    phase: tuple[int, int]
    score: tuple[Any, ...] = field(default=())


def _names_of_type(gd: GameData, type_: str) -> list[str]:
    return sorted(n for n, e in gd.entities.items() if e.type == type_)


def _check_names(p: MiningPatchParams, gd: GameData) -> None:
    def need(name: str, type_: str, key: str) -> None:
        e = gd.entities.get(name)
        if e is None or e.type != type_:
            opts = [f"{key}={o!r}" for o in _names_of_type(gd, type_)]
            raise Infeasible(f"{key}={name!r} is not a {type_}", opts)

    if p.resource not in gd.resources:
        raise Infeasible(
            f"unknown resource {p.resource!r}", [f"resource={r!r}" for r in sorted(gd.resources)]
        )
    need(p.drill, "mining-drill", "drill")
    need(p.belt, "transport-belt", "belt")
    need(p.pole, "electric-pole", "pole")
    if p.axis not in ("x", "y") or p.flow not in ("+", "-"):
        raise Infeasible("axis must be 'x' or 'y' and flow '+' or '-'")
    if p.min_ore_tiles_per_drill < 1:
        raise Infeasible("min_ore_tiles_per_drill must be >= 1", ["min_ore_tiles_per_drill=1"])
    if p.min_ore_amount_per_drill < 0:
        raise Infeasible("min_ore_amount_per_drill must be >= 0", ["min_ore_amount_per_drill=0"])
    if p.max_belts is not None and p.max_belts < 1:
        raise Infeasible("max_belts must be >= 1 or None", ["max_belts=None"])
    res = gd.resource(p.resource)
    if res.infinite or res.required_fluid is not None:
        raise Infeasible(
            f"{p.resource} needs {'a fluid input' if res.required_fluid else 'a fluid output'};"
            " this family mines solid ore only"
        )
    e = gd.entity(p.drill)
    if not e.is_electric:
        ok = [n for n in _names_of_type(gd, "mining-drill") if _usable_drill(gd, n, p.resource)]
        raise Infeasible(f"{p.drill} is not electric", [f"drill={n!r}" for n in ok])
    if e.vector_to_place_result is None or not mining.can_mine(gd, p.drill, p.resource):
        ok = [n for n in _names_of_type(gd, "mining-drill") if _usable_drill(gd, n, p.resource)]
        raise Infeasible(f"{p.drill} cannot mine {p.resource}", [f"drill={n!r}" for n in ok])


def _usable_drill(gd: GameData, name: str, resource: str) -> bool:
    e = gd.entity(name)
    return (
        e.is_electric
        and e.vector_to_place_result is not None
        and mining.can_mine(gd, name, resource)
    )


def _geometry(p: MiningPatchParams, gd: GameData) -> _Geo:
    rows: dict[str, tuple[int, int]] = {}
    anchor_dx: dict[str, int] = {}
    lanes: dict[str, Lane] = {}
    w = None
    for side in SIDES:
        d = FACING[side]
        dw, dh = footprint.size(gd, p.drill, d)
        c = footprint.center_for_tile_anchor(gd, p.drill, Tile(0, 0), d)
        dt = mining.drop_tile(gd, p.drill, c, d)
        # drill anchor relative to (drop column, belt row)
        top = -dt.y
        if side == "north" and not top + dh - 1 < 0:
            raise Infeasible(f"{p.drill} facing S does not drop below itself")
        if side == "south" and not top > 0:
            raise Infeasible(f"{p.drill} facing N does not drop above itself")
        if not 0 <= dt.x < dw:
            raise Infeasible(f"{p.drill} drops outside its own columns")
        rows[side] = (top, dh)
        anchor_dx[side] = -dt.x
        lanes[side] = mining.drop_lane(gd, p.drill, c, d, FLOW)
        if w is not None and dw != w:
            raise Infeasible(f"{p.drill} is not square")
        w = dw
    assert w is not None
    if lanes["north"] == lanes["south"]:
        raise Infeasible(f"both drill rows of {p.drill} drop onto the same lane")
    n_top = rows["north"][0]
    s_top, s_h = rows["south"]
    period = s_top + s_h - n_top

    # pole anchor offsets inside a slot, measured relative to belt row 0 / drop column 0
    pw, ph = footprint.size(gd, p.pole)
    if pw > w or ph > max(rows["north"][1], s_h):
        raise Infeasible(f"{p.pole} does not fit in a {p.drill} slot")

    def side_rows(side: str) -> tuple[int, int]:
        top, h = rows[side]
        return top, top + h - 1

    def offsets(side: str) -> list[tuple[int, int, set[str]]]:
        top, h = rows[side]
        out = []
        for oy in range(h - ph + 1):
            for ox in range(w - pw + 1):
                a = Tile(anchor_dx[side] + ox, top + oy)
                cen = footprint.center_for_tile_anchor(gd, p.pole, a)
                _, y0, _, y1 = pole.supply_range(gd, p.pole, cen)
                reach = {s for s in SIDES if side_rows(s)[0] <= y1 and side_rows(s)[1] >= y0}
                out.append((ox, oy, reach))
        mid = (w - pw) / 2
        # nearest the belt first, then the middle column
        out.sort(key=lambda o: (abs(rows[side][0] + o[1] + (ph - 1) / 2), abs(o[0] - mid)))
        return out

    def centre_y(side: str, oy: int) -> float:
        a = Tile(anchor_dx[side], rows[side][0] + oy)
        return footprint.center_for_tile_anchor(gd, p.pole, a).y

    wire = pole.wire_distance(gd, p.pole, p.pole)
    # pole spacing: the gap every ``s`` columns; ``s - pw`` columns hold ``(s - pw) // w``
    # drills, so take the spacing with the most drills per column (ties: the longer one)
    if pw + w > math.floor(wire + _EPS):
        raise Infeasible(f"{p.pole} wire reach {wire:g} is shorter than a drill plus the pole")
    s = max(
        range(pw + w, math.floor(wire + _EPS) + 1),
        key=lambda n: (((n - pw) // w) / n, n),
    )
    both = {s_: [o for o in offsets(s_) if set(SIDES) <= o[2]] for s_ in SIDES}
    # one pole row per belt when one in-slot spot supplies both drill rows and the same
    # spot one belt period further is still in wire reach
    one = [s_ for s_ in SIDES if both[s_] and period <= wire + _EPS]
    if one:
        side = one[0]
        pole_offsets = {side: [(ox, oy) for ox, oy, _ in both[side] if ox == 0]}
        sides: tuple[str, ...] = (side,)
    else:
        own = {s_: [o for o in offsets(s_) if s_ in o[2] and o[0] == 0] for s_ in SIDES}
        if not all(own.values()):
            raise Infeasible(f"{p.pole} in a drill row cannot reach the drills next to it")
        # a pole row on each side; pick the rows that keep the longest vertical hop short
        best = None
        for on in own["north"]:
            for os_ in own["south"]:
                h1 = abs(centre_y("south", os_[1]) - centre_y("north", on[1]))
                hop = max(h1, period - h1)
                key = (hop, own["north"].index(on) + own["south"].index(os_))
                if best is None or key < best[0]:
                    best = (key, on, os_)
        assert best is not None
        _, on, os_ = best
        pole_offsets = {
            "north": [(on[0], on[1])] + [(o[0], o[1]) for o in own["north"] if o != on],
            "south": [(os_[0], os_[1])] + [(o[0], o[1]) for o in own["south"] if o != os_],
        }
        sides = SIDES
    return _Geo(w, period, rows, anchor_dx, lanes, pole_offsets, sides, pw, s)


def _drill_anchor(g: _Geo, side: str, x: int, yb: int) -> Tile:
    return Tile(x + g.anchor_dx[side], yb + g.rows[side][0])


def _pole_col(g: _Geo, phase_x: int, m: int) -> int:
    """Left column of the pole gap ``m`` (absolute tile x)."""
    return phase_x + g.s * m


def _drill_columns(g: _Geo, side: str, phase: tuple[int, int], x0: int, x1: int) -> list[int]:
    """Drop columns of the drills on ``side`` within ``[x0, x1]``.

    A side with poles is cut by pole gaps: between two gaps (``s`` apart, ``pw`` wide)
    drills sit back to back from the gap's right edge. Other sides are a plain grid.
    """
    px = phase[0]
    if side not in g.pole_sides:
        return [x for x in range(x0, x1 + 1) if (x - px) % g.w == 0]
    out: list[int] = []
    for m in range(math.floor((x0 - px) / g.s) - 1, math.ceil((x1 - px) / g.s) + 2):
        gap = _pole_col(g, px, m)
        left = gap + g.pw  # footprint left edge of the first drill after the gap
        while left + g.w - 1 <= gap + g.s - 1:
            x = left - g.anchor_dx[side]
            if x0 <= x <= x1:
                out.append(x)
            left += g.w
    return out


def _candidates(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    ore: frozenset[Tile],
    phase: tuple[int, int],
) -> tuple[dict[int, int], list[_Cand], list[dict[str, Any]]]:
    """Belt rows (index -> y) and candidate drills; drops for blocked belts."""
    lo, hi = bounds_of(ore)
    ox0, oy0, ox1, oy1 = lo.x, lo.y, hi.x, hi.y
    r = gd.entity(p.drill).resource_searching_radius or 0
    margin = math.ceil(r) + g.w + g.period + g.s
    py = phase[1]
    ys = [y for y in range(oy0 - margin, oy1 + margin + 1) if (y - py) % g.period == 0]
    rows = dict(enumerate(ys))
    count = _counter({t: 1 for t in ore})
    amount = _counter({t: terr.resources[t][1] for t in ore if t in terr.resources})
    window = (ox0 - margin, oy0 - margin, ox1 + margin, oy1 + margin)
    cats = set(gd.entity(p.drill).resource_categories)
    other = {
        t: 1
        for t, (name, _) in terr.resources.items()
        if name != p.resource
        and window[0] <= t.x <= window[2]
        and window[1] <= t.y <= window[3]
        and (name not in gd.resources or gd.resource(name).category in cats)
    }
    mixed = _counter(other) if other else None
    cands: list[_Cand] = []
    dropped: list[dict[str, Any]] = []
    for i, yb in rows.items():
        for side in SIDES:
            d = FACING[side]
            for x in _drill_columns(g, side, phase, ox0 - margin, ox1 + margin):
                a = _drill_anchor(g, side, x, yb)
                pos = footprint.center_for_tile_anchor(gd, p.drill, a, d)
                box = mining.search_range(gd, p.drill, pos)
                n = count(*box)
                if n < p.min_ore_tiles_per_drill:
                    continue
                ts = footprint.tiles_of(gd, p.drill, pos, d)
                if not terr.can_build_all(ts) or not terr.can_build(Tile(x, yb)):
                    continue
                c = _Cand(i, side, 0, x, pos, n, g.lanes[side])
                if amount(*box) < p.min_ore_amount_per_drill:
                    dropped.append(_drop(c, "low_ore", f"{amount(*box)} ore in its area"))
                elif mixed is not None and not p.allow_mixed and mixed(*box):
                    dropped.append(_drop(c, "mixed_resource"))
                else:
                    cands.append(c)
    # a belt must run from each drill to the row's downstream end
    kept: list[_Cand] = []
    for i, yb in rows.items():
        cs = [c for c in cands if c.row == i]
        if not cs:
            continue
        end = max(c.x for c in cs)
        lo = min(c.x for c in cs)
        blocked = [x for x in range(lo, end + 1) if not terr.can_build(Tile(x, yb))]
        cut = max(blocked) if blocked else None
        for c in cs:
            if cut is not None and c.x < cut:
                dropped.append(_drop(c, "belt_blocked", f"belt blocked at ({cut},{yb})"))
            else:
                kept.append(c)
    return rows, kept, dropped


def _counter(weights: dict[Tile, int]):  # -> Callable[[int, int, int, int], int]
    """O(1) sum of ``weights`` over an inclusive tile rectangle (2D prefix sums)."""
    if not weights:
        return lambda x0, y0, x1, y1: 0
    lo, hi = bounds_of(weights)
    w, h = hi.x - lo.x + 1, hi.y - lo.y + 1
    pre = [[0] * (w + 1) for _ in range(h + 1)]
    for j in range(h):
        row, above = pre[j + 1], pre[j]
        acc = 0
        for i in range(w):
            acc += weights.get(Tile(lo.x + i, lo.y + j), 0)
            row[i + 1] = above[i + 1] + acc

    def count(x0: int, y0: int, x1: int, y1: int) -> int:
        x0, y0 = max(x0 - lo.x, 0), max(y0 - lo.y, 0)
        x1, y1 = min(x1 - lo.x, w - 1), min(y1 - lo.y, h - 1)
        if x0 > x1 or y0 > y1:
            return 0
        return pre[y1 + 1][x1 + 1] - pre[y0][x1 + 1] - pre[y1 + 1][x0] + pre[y0][x0]

    return count


def _drop(c: _Cand, reason: str, detail: str = "") -> dict[str, Any]:
    out = {"position": [c.pos.x, c.pos.y], "row": c.row, "side": c.side, "reason": reason}
    if detail:
        out["detail"] = detail
    return out


def _pole_slots(g: _Geo, cands: list[_Cand], phase_x: int) -> list[int]:
    """Pole gap indices ``m`` (see ``_pole_col``) whose supply can reach some candidate."""
    if not cands:
        return []
    lo = min(c.x + g.anchor_dx[c.side] for c in cands)
    hi = max(c.x + g.anchor_dx[c.side] + g.w - 1 for c in cands)
    return list(range(math.floor((lo - phase_x) / g.s) - 1, math.ceil((hi - phase_x) / g.s) + 2))


def _place_poles(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    rows: dict[int, int],
    cands: list[_Cand],
    slots: list[int],
    phase_x: int,
) -> list[Placement]:
    """Poles in the pole gaps, per belt row: only over the row's drills, extended so that
    neighbouring rows share a gap (same column in every row, so the rows connect)."""
    if not slots:
        return []
    by_row: dict[int, list[_Cand]] = {}
    for c in cands:
        by_row.setdefault(c.row, []).append(c)
    used = sorted(by_row)

    def window(j: int) -> tuple[int, int]:
        cx = _pole_col(g, phase_x, j) + g.pw / 2
        x0, _, x1, _ = pole.supply_range(gd, p.pole, Vec2(cx, 0.5))
        return x0, x1

    intervals: dict[int, tuple[int, int]] = {}
    for r in used:
        need = [
            j
            for j in slots
            if any(
                window(j)[0] <= c.x + g.anchor_dx[c.side] + g.w - 1
                and window(j)[1] >= c.x + g.anchor_dx[c.side]
                for c in by_row[r]
            )
        ]
        intervals[r] = (min(need), max(need))
    # rows in between used rows get a bridge pole; neighbouring intervals must overlap
    order = list(range(used[0], used[-1] + 1))
    prev: tuple[int, int] | None = None
    final: dict[int, tuple[int, int]] = {}
    for r in order:
        if r in intervals:
            a, b = intervals[r]
            if prev is not None:
                if b < prev[0]:
                    b = prev[0]
                if a > prev[1]:
                    a = prev[1]
            final[r] = (a, b)
            prev = final[r]
        else:
            assert prev is not None
            final[r] = (prev[1], prev[1])
            prev = final[r]
    poles: list[Placement] = []
    _, ph = footprint.size(gd, p.pole)
    for r, (a, b) in final.items():
        yb = rows[r]
        for j in slots:
            if not a <= j <= b:
                continue
            for side in g.pole_sides:
                top, h = g.rows[side]
                ax = _pole_col(g, phase_x, j)
                oys = list(dict.fromkeys([oy for _, oy in g.pole_offsets[side]]))
                oys += [oy for oy in range(h - ph + 1) if oy not in oys]
                for oy in oys:
                    anchor = Tile(ax, yb + top + oy)
                    cen = footprint.center_for_tile_anchor(gd, p.pole, anchor)
                    if terr.can_build_all(footprint.tiles_of(gd, p.pole, cen, Direction.N)):
                        poles.append(Placement(p.pole, cen))
                        break
    return poles


def _build(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    ore: frozenset[Tile],
    phase: tuple[int, int],
) -> _Result | None:
    rows, cands, dropped = _candidates(p, gd, g, terr, ore, phase)
    if p.max_belts is not None:
        counts = {r: sum(1 for c in cands if c.row == r) for r in rows}
        best = max(
            range(len(rows)),
            key=lambda s: (sum(counts.get(r, 0) for r in range(s, s + p.max_belts)), -s),
        )
        keep_rows = set(range(best, best + p.max_belts))
        for c in cands:
            if c.row not in keep_rows:
                dropped.append(_drop(c, "max_belts"))
        cands = [c for c in cands if c.row in keep_rows]
    if not cands:
        return _Result([], [], dropped, 0, [], phase, (0,))
    slots = _pole_slots(g, cands, phase[0])
    poles = _place_poles(p, gd, g, terr, rows, cands, slots, phase[0])
    # a drill stays only if some pole's supply area covers its footprint
    supply: set[Tile] = set()
    for q in poles:
        supply |= pole.supply_tiles(gd, p.pole, q.position)
    kept: list[_Cand] = []
    for c in cands:
        fp = footprint.tiles_of(gd, p.drill, c.pos, FACING[c.side])
        if fp & supply:
            kept.append(c)
        else:
            dropped.append(_drop(c, "pole_slot", "no pole supplies its footprint"))
    # lane capacity: one lane per side, cut from the upstream end
    rate = mining.drill_rate(gd, p.drill, p.resource)
    cap = tp.belt_lane_capacity(gd, p.belt)
    per_lane = math.floor(cap / rate + _EPS)
    final: list[_Cand] = []
    for r in rows:
        for lane in ("left", "right"):
            cs = sorted((c for c in kept if c.row == r and c.lane == lane), key=lambda c: -c.x)
            final += cs[:per_lane]
            for c in cs[per_lane:]:
                dropped.append(
                    _drop(
                        c,
                        "lane_capacity",
                        f"{len(cs)} drills x {rate:g}/s > {p.belt} lane {cap:g}/s",
                    )
                )
    placements: list[Placement] = []
    belts: list[dict[str, Any]] = []
    for r, yb in rows.items():
        cs = [c for c in final if c.row == r]
        if not cs:
            continue
        for c in sorted(cs, key=lambda c: (c.x, c.side)):
            placements.append(Placement(p.drill, c.pos, FACING[c.side]))
        x0, x1 = min(c.x for c in cs), max(c.x for c in cs)
        for x in range(x0, x1 + 1):
            placements.append(
                Placement(
                    p.belt, footprint.center_for_tile_anchor(gd, p.belt, Tile(x, yb), FLOW), FLOW
                )
            )
        lanes = {lane: sum(1 for c in cs if c.lane == lane) for lane in ("left", "right")}
        belts.append(
            {
                "row": r,
                "y": yb,
                "x": [x0, x1],
                "drills": len(cs),
                "lanes": lanes,
                "rate": len(cs) * rate,
                "ore_tiles": sum(c.ore for c in cs),
            }
        )
    placements += poles
    n = len(final)
    cap_drops = sum(1 for d in dropped if d["reason"] == "lane_capacity")
    score = (n, -cap_drops, -len(poles), -phase[1], -phase[0])
    return _Result(placements, belts, dropped, n, poles, phase, score)


def _transform_terrain(terr: Terrain, rot: Direction | None) -> Terrain:
    if rot is None:
        return terr

    def f(t: Tile) -> Tile:
        return t.rotate(rot)

    return Terrain(
        buildable=frozenset(map(f, terr.buildable)),
        resources={f(t): v for t, v in terr.resources.items()},
        water=frozenset(map(f, terr.water)),
        occupied=frozenset(map(f, terr.occupied)),
    )


def _fmt(change: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in change.items())


class MiningPatch:
    family = "anchor/mining-patch"

    def generate(
        self,
        params: MiningPatchParams,
        gd: GameData,
        patch: Patch,
        terrain: Terrain,
        *,
        _suggest: bool = True,
    ) -> Unit:
        p = params
        _check_names(p, gd)
        if patch.resource != p.resource:
            raise Infeasible(
                f"patch is {patch.resource}, params ask for {p.resource}",
                [f"resource={patch.resource!r}"],
            )
        g = _geometry(p, gd)
        rot = _TO_CANON[(p.axis, p.flow)]
        terr = _transform_terrain(terrain, rot)
        ore = frozenset(t.rotate(rot) for t in patch.tiles) if rot is not None else patch.tiles
        best: _Result | None = None
        power_failed = False
        for py in range(g.period):
            for px in range(g.s):
                res = _build(p, gd, g, terr, ore, (px, py))
                if res is None:
                    power_failed = True
                    continue
                if best is None or res.score > best.score:
                    best = res
        if best is None or best.drills == 0:
            if power_failed and best is None:
                alts = self._alternatives(
                    p,
                    gd,
                    patch,
                    terrain,
                    [{"pole": n} for n in _names_of_type(gd, "electric-pole") if n != p.pole],
                )
                raise Infeasible(
                    f"{p.pole} cannot chain along the drill rows", [_fmt(c) for c in alts], alts
                )
            changes: list[dict[str, Any]] = [{"axis": "y" if p.axis == "x" else "x"}]
            if p.min_ore_tiles_per_drill > 1:
                changes.append({"min_ore_tiles_per_drill": 1})
            if p.min_ore_amount_per_drill > 0:
                changes.append({"min_ore_amount_per_drill": 0})
            if not p.allow_mixed:
                changes.append({"allow_mixed": True})
            changes += [
                {"drill": n}
                for n in _names_of_type(gd, "mining-drill")
                if n != p.drill and _usable_drill(gd, n, p.resource)
            ]
            alts = self._alternatives(p, gd, patch, terrain, changes) if _suggest else []
            raise Infeasible(
                f"no {p.drill} fits on the {len(patch.tiles)}-tile patch with buildable "
                "footprint and belt",
                [_fmt(c) for c in alts],
                alts,
            )
        unit = self._unit(p, gd, g, best, patch, rot)
        if _suggest:
            unit = self._add_suggestions(p, gd, patch, terrain, unit)
        violations = self.invariants(unit, gd, terrain)
        if violations:
            detail = "; ".join(f"{v.code}: {v.message}" for v in violations[:5])
            if any(v.code.startswith("power.") for v in violations):
                alts = (
                    self._alternatives(
                        p,
                        gd,
                        patch,
                        terrain,
                        [{"pole": n} for n in _names_of_type(gd, "electric-pole") if n != p.pole],
                    )
                    if _suggest
                    else []
                )
                raise Infeasible(
                    f"generated layout violates invariants: {detail}", [_fmt(c) for c in alts], alts
                )
            raise Infeasible(f"generated layout violates invariants: {detail}")
        return unit

    def _alternatives(
        self,
        p: MiningPatchParams,
        gd: GameData,
        patch: Patch,
        terrain: Terrain,
        changes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ok = []
        for ch in changes:
            try:
                self.generate(replace(p, **ch), gd, patch, terrain, _suggest=False)
            except Infeasible:
                continue
            ok.append(ch)
        return ok

    def _add_suggestions(
        self, p: MiningPatchParams, gd: GameData, patch: Patch, terrain: Terrain, unit: Unit
    ) -> Unit:
        def cap_drops(u: Unit) -> int:
            return sum(1 for d in u.meta["dropped_drills"] if d["reason"] == "lane_capacity")

        lost = cap_drops(unit)
        if not lost:
            return unit
        out: list[dict[str, Any]] = []
        notes: list[str] = []
        cap = tp.belt_lane_capacity(gd, p.belt)
        for b in sorted(
            (
                n
                for n in _names_of_type(gd, "transport-belt")
                if tp.belt_lane_capacity(gd, n) > cap + _EPS
            ),
            key=lambda n: tp.belt_lane_capacity(gd, n),
        ):
            try:
                u = self.generate(replace(p, belt=b), gd, patch, terrain, _suggest=False)
            except Infeasible:
                continue
            if cap_drops(u) < lost:
                out.append({"belt": b})
                notes.append(f"belt={b!r}: {u.meta['anchor']['drills']} drills, {cap_drops(u)} cut")
                if cap_drops(u) == 0:
                    break
        other = "y" if p.axis == "x" else "x"
        try:
            u = self.generate(replace(p, axis=other), gd, patch, terrain, _suggest=False)
            if cap_drops(u) < lost:
                out.append({"axis": other})
                notes.append(
                    f"axis={other!r}: {u.meta['anchor']['drills']} drills, {cap_drops(u)} cut"
                )
        except Infeasible:
            pass
        meta = dict(unit.meta)
        meta["suggestion_changes"] = out
        meta["suggestions"] = [_fmt(c) for c in out]
        meta["suggestion_notes"] = notes
        return Unit(unit.layout, unit.interfaces, meta)

    def _unit(
        self,
        p: MiningPatchParams,
        gd: GameData,
        g: _Geo,
        res: _Result,
        patch: Patch,
        rot: Direction | None,
    ) -> Unit:
        rate = mining.drill_rate(gd, p.drill, p.resource)
        product = gd.resource(p.resource).product
        cap = tp.belt_lane_capacity(gd, p.belt)
        ifaces: list[Interface] = []
        for b in res.belts:
            lanes = [lane for lane, n in b["lanes"].items() if n]
            lane = "both" if len(lanes) == 2 else lanes[0]
            per = ", ".join(
                f"{lane} lane {n} drills = {n * rate:g}/s" for lane, n in b["lanes"].items()
            )
            ifaces.append(
                Interface(
                    "item",
                    product,
                    Tile(b["x"][1], b["y"]),
                    FLOW,
                    lane,  # type: ignore[arg-type]
                    b["rate"],
                    "output",
                    [
                        "mining productivity bonus 0 (no research)",
                        "every drill has ore under it (rate drops as edge drills deplete)",
                        f"{per}; each lane carries at most {cap:g}/s on {p.belt}",
                        "downstream keeps the belt moving",
                    ],
                )
            )
        if res.poles:
            first = min(res.poles, key=lambda q: (q.position.x, q.position.y))
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
        unit = Unit(Layout(res.placements), ifaces, {})
        dropped = res.dropped
        if rot is not None:
            back = _INVERSE[rot]
            unit = tf.rotate(unit, back)

            def unrot(pos: list[float]) -> list[float]:
                v = Vec2(pos[0], pos[1]).rotate(back)
                return [v.x, v.y]

            dropped = [{**d, "position": unrot(d["position"])} for d in dropped]
        total_rate = sum(b["rate"] for b in res.belts)
        lo, hi = patch.bounds
        covered: set[Tile] = set()
        areas = [
            mining.search_tiles(gd, p.drill, pl.position) & patch.tiles
            for pl in unit.layout.placements
            if pl.name == p.drill
        ]
        sharers: dict[Tile, int] = {}
        for a in areas:
            covered |= a
            for t in a:
                sharers[t] = sharers.get(t, 0) + 1
        covered_amount = sum(a for t, a in patch.amounts.items() if t in covered)
        # no drill can run dry before its own ore is gone even if every neighbour sharing
        # a tile mined only from its area: amount / (rate x most sharers on one tile)
        first_dry = min(
            (
                sum(patch.amounts.get(t, 0) for t in a) / (rate * max(sharers[t] for t in a))
                for a in areas
                if a
            ),
            default=None,
        )
        # per output: the earliest any of its drills can run dry (same bound)
        drills = [pl for pl in unit.layout.placements if pl.name == p.drill]
        belt_tiles = {
            pl.position.floor()
            for pl in unit.layout.placements
            if gd.entity(pl.name).type == "transport-belt"
        }
        dry_by_tile: dict[Tile, float] = {}
        for pl, a in zip(drills, areas, strict=True):
            if a:
                dry_by_tile[mining.drop_tile(gd, p.drill, pl.position, pl.direction)] = sum(
                    patch.amounts.get(t, 0) for t in a
                ) / (rate * max(sharers[t] for t in a))
        new_ifaces = []
        for i in unit.interfaces:
            if i.kind == "item" and i.role == "output" and i.rate is not None:
                t, bound = i.tile, None
                while t in belt_tiles:
                    if t in dry_by_tile:
                        v = dry_by_tile[t]
                        bound = v if bound is None else min(bound, v)
                    t = t.neighbor(i.direction.opposite)
                i = replace(i, rate_profile=RateProfile(i.rate, bound))
            new_ifaces.append(i)
        unit = Unit(unit.layout, new_ifaces, unit.meta)
        reasons: dict[str, int] = {}
        for d in dropped:
            reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        meta = {
            "family": self.family,
            "params": asdict(p),
            "machine": p.drill,
            "icon": product,
            "drill_rate": rate,
            "lane_capacity": cap,
            "anchor": {
                "resource": p.resource,
                "patch_bounds": [[lo.x, lo.y], [hi.x, hi.y]],
                "patch_tiles": len(patch.tiles),
                "total_amount": patch.total_amount,
                "covered_amount": covered_amount,
                "drills": res.drills,
                "rate": total_rate,
                "est_depletion_s": patch.total_amount / total_rate if total_rate else None,
                "est_depletion_covered_s": covered_amount / total_rate if total_rate else None,
                "first_dry_s_lower_bound": first_dry,
            },
            "belts": [
                {
                    "drills": b["drills"],
                    "lanes": b["lanes"],
                    "rate": b["rate"],
                    "output": [i.tile.x, i.tile.y],
                }
                for b, i in zip(res.belts, unit.interfaces, strict=False)
            ],
            "poles": len(res.poles),
            "phase": list(res.phase),
            "dropped_drills": dropped,
            "dropped_by_reason": reasons,
            "suggestions": [],
            "suggestion_changes": [],
        }
        return Unit(unit.layout, unit.interfaces, meta)

    def invariants(
        self, unit: Unit, gd: GameData, terrain: Terrain | None = None
    ) -> list[Violation]:
        """``validate_unit`` (with ``drill.no_ore`` against the terrain's resources) plus
        ``terrain.unbuildable``: an entity on a tile the terrain does not allow."""
        out = validate_unit(unit, gd, terrain.resources if terrain is not None else None)
        if terrain is not None:
            for i, pl in enumerate(unit.layout.placements):
                bad = frozenset(t for t in pl.tiles(gd) if not terrain.can_build(t))
                if bad:
                    out.append(
                        Violation(
                            "terrain.unbuildable", f"#{i} {pl.name} on unbuildable tiles", bad
                        )
                    )
        return out


def generate(params: MiningPatchParams, gd: GameData, patch: Patch, terrain: Terrain) -> Unit:
    return MiningPatch().generate(params, gd, patch, terrain)


def render(
    unit: Unit, gd: GameData, terrain: Terrain, *, rulers: bool = True, margin: int = 2
) -> str:
    """ASCII of the unit over its terrain (``:`` ore, ``~`` water, ``x`` blocked)."""
    resource = unit.meta.get("anchor", {}).get("resource")
    tiles = set(unit.layout.tiles(gd))
    pb = unit.meta.get("anchor", {}).get("patch_bounds")
    if pb:
        tiles |= {Tile(*pb[0]), Tile(*pb[1])}
    (lo, hi) = bounds_of(tiles)
    area: Area = (lo.translate(-margin, -margin), hi.translate(margin, margin))
    return to_ascii(unit.layout, gd, rulers=rulers, background=background(terrain, resource, area))
