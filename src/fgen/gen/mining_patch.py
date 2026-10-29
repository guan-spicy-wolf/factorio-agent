"""Generator family 2: drills over an ore patch (anchor unit, plan-phase2 §3.2).

Canonical frame (``axis="x"``, ``flow="+"``): belts run east along rows; each belt row
has a row of drills on each side facing it (north side faces S, south side faces N), so
every drill drops onto the belt (the drop tile comes from ``vector_to_place_result``,
the lane from where the drop point lies inside the belt tile: the drill's own side).
With the electric drill::

    DDDDDDPDDDDDD      north drill row (3 tall), P = pole that replaced one drill slot
    DDDDDD.DDDDDD
    DDDDDDDDDDDDD
    >>>>>>>>>>>>>      belt row: flows east; its east end is an output interface
    DDDDDDDDDDDDD
    DDDDDDDDDDDDD      south drill row
    DDDDDDDDDDDDD

Belt rows repeat every ``period`` = drill rows + belt + drill rows (7 for the 3x3
drill). Other axes/flows are the canonical frame rotated (``layout.transform``).

Derived from ``GameData`` (no entity names in the logic):

- drill positions: drop tile and footprint from the prototype; a slot gets a drill when
  its search area (``fgen.geom.mining.search_tiles``) holds at least
  ``min_ore_tiles_per_drill`` patch tiles and ``min_ore_amount_per_drill`` ore, no other
  resource the drill would mine too (unless ``allow_mixed``; reasons ``low_ore`` /
  ``mixed_resource``), and its footprint and belt tile are buildable;
- the y phase of the belt rows (``period`` options) is searched for the most drills; along
  a row drills are not on a column grid;
- poles: a pole takes only its own columns in one side's drill rows (the spot reaching the
  most sides' drill rows, nearest the belt). Per belt row a dynamic programme chooses the
  pole sequence (consecutive poles within ``maximum_wire_distance``, either side) together
  with the drills: between two poles each side is packed greedily with the drills that
  touch one of the two supply areas. Rows are chained (each row's chain must link to the
  rows above; empty rows get bridge poles). ``meta["dropped_drills"]`` reason
  ``pole_slot`` counts drills lost against the pole-free packing of the same patch;
- lane capacity: each lane carries the drills of one side; drills beyond
  ``lane capacity / drill rate`` are cut from the upstream end (reason
  ``lane_capacity``) and ``meta["suggestions"]`` names a belt tier or axis that keeps them.
"""

from __future__ import annotations

import bisect
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
    a: int  # leftmost drill column (canonical frame)
    x: int  # drop column (= belt tile x)
    pos: Vec2
    ore: int  # patch tiles in the search area
    lane: Lane


@dataclass(frozen=True)
class _PoleSpec:
    """Where a pole sits in the drill rows of one side, and what it supplies."""

    oy: int  # row offset inside the side's drill rows
    reach: frozenset[str]  # sides whose drill rows its supply area touches
    ycen: float  # centre y relative to the belt row
    xcen: float  # centre x of the pole anchored at column 0
    lo: int  # supply columns relative to the pole's anchor column: lo..hi
    hi: int
    width: int  # footprint width (columns it blocks)


@dataclass
class _Geo:
    """Per-parameter geometry in the canonical frame (independent of the patch)."""

    w: int  # drill width along the belt
    period: int
    rows: dict[str, tuple[int, int]]  # side -> drill top-row offset from belt row, height
    anchor_dx: dict[str, int]  # side -> drill anchor x - drop column
    lanes: dict[str, Lane]
    poles: dict[str, _PoleSpec]  # side -> the pole spot used on that side
    wire: float


@dataclass
class _Result:
    placements: list[Placement]
    belts: list[dict[str, Any]]
    dropped: list[dict[str, Any]]
    drills: int
    poles: list[Placement]
    phase: tuple[int, ...]
    score: tuple[Any, ...] = field(default=())
    free: list[_Cand] = field(default_factory=list)  # drills of the pole-free packing
    final: list[_Cand] = field(default_factory=list)


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

    # where a pole can stand: any tile of a side's drill rows (a free column there costs
    # only the columns of the pole, not a whole drill slot). Per side take the spot that
    # reaches the most sides' drill rows, nearest the belt.
    pw, ph = footprint.size(gd, p.pole)
    if pw > w or ph > max(rows["north"][1], s_h):
        raise Infeasible(f"{p.pole} does not fit in a {p.drill} slot")
    specs: dict[str, _PoleSpec] = {}
    for side in SIDES:
        top, h = rows[side]
        best: tuple[Any, ...] | None = None
        for oy in range(h - ph + 1):
            cen = footprint.center_for_tile_anchor(gd, p.pole, Tile(0, top + oy))
            x0, y0, x1, y1 = pole.supply_range(gd, p.pole, cen)
            reach = frozenset(
                s for s in SIDES if rows[s][0] <= y1 and rows[s][0] + rows[s][1] - 1 >= y0
            )
            if side not in reach:
                continue
            key = (-len(reach), abs(top + oy + (ph - 1) / 2))
            if best is None or key < best[0]:
                best = (key, _PoleSpec(oy, reach, cen.y, cen.x, x0, x1, pw))
        if best is None:
            raise Infeasible(f"{p.pole} in a drill slot cannot reach the drills next to it")
        specs[side] = best[1]
    wire = pole.wire_distance(gd, p.pole, p.pole)
    return _Geo(w, period, rows, anchor_dx, lanes, specs, wire)


def _drill_anchor(g: _Geo, side: str, x: int, yb: int) -> Tile:
    return Tile(x + g.anchor_dx[side], yb + g.rows[side][0])


def _candidates(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    ore: frozenset[Tile],
    py: int,
) -> tuple[dict[int, int], dict[tuple[int, str], dict[int, _Cand]], list[dict[str, Any]]]:
    """Belt rows (index -> y), candidate drills per (row, side) by leftmost column, and
    drops for blocked belts. Drills are not tied to a column grid: poles take single
    columns, so the packing along a row is free."""
    lo, hi = bounds_of(ore)
    ox0, oy0, ox1, oy1 = lo.x, lo.y, hi.x, hi.y
    r = gd.entity(p.drill).resource_searching_radius or 0
    margin = math.ceil(r) + g.w + g.period
    ys = [y for y in range(oy0 - margin, oy1 + margin + 1) if (y - py) % g.period == 0]
    xs = list(range(ox0 - margin, ox1 + margin + 1))
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
    cands: dict[tuple[int, str], dict[int, _Cand]] = {}
    dropped: list[dict[str, Any]] = []
    for i, yb in rows.items():
        for side in SIDES:
            d = FACING[side]
            for x in xs:
                a = _drill_anchor(g, side, x, yb)
                pos = footprint.center_for_tile_anchor(gd, p.drill, a, d)
                box = mining.search_range(gd, p.drill, pos)
                n = count(*box)
                if n < p.min_ore_tiles_per_drill:
                    continue
                ts = footprint.tiles_of(gd, p.drill, pos, d)
                if not terr.can_build_all(ts) or not terr.can_build(Tile(x, yb)):
                    continue
                c = _Cand(i, side, a.x, x, pos, n, g.lanes[side])
                if amount(*box) < p.min_ore_amount_per_drill:
                    dropped.append(_drop(c, "low_ore", f"{amount(*box)} ore in its area"))
                elif mixed is not None and not p.allow_mixed and mixed(*box):
                    dropped.append(_drop(c, "mixed_resource"))
                else:
                    cands.setdefault((i, side), {})[a.x] = c
    # a belt must run from each drill to the row's downstream end
    for i, yb in rows.items():
        cs = [c for side in SIDES for c in cands.get((i, side), {}).values()]
        if not cs:
            continue
        end = max(c.x for c in cs)
        lo_x = min(c.x for c in cs)
        blocked = [x for x in range(lo_x, end + 1) if not terr.can_build(Tile(x, yb))]
        if not blocked:
            continue
        cut = max(blocked)
        for side in SIDES:
            for a, c in list(cands.get((i, side), {}).items()):
                if c.x < cut:
                    dropped.append(_drop(c, "belt_blocked", f"belt blocked at ({cut},{yb})"))
                    del cands[(i, side)][a]
    return rows, cands, dropped


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


def _pack(
    cands: list[int], w: int, e: int, a_max: int, ranges: list[tuple[int, int]]
) -> tuple[list[int], int]:
    """Greedy (earliest end) packing of drills of width ``w`` at the sorted candidate
    columns ``cands``: start >= ``e``, start <= ``a_max``, and each touching one of the
    supply column ``ranges``. Optimal in count and in where the last drill ends."""
    out: list[int] = []
    i = bisect.bisect_left(cands, e)
    while i < len(cands):
        a = cands[i]
        if a > a_max:
            break
        if any(a <= x1 and a + w - 1 >= x0 for x0, x1 in ranges):
            out.append(a)
            e = a + w
            i = bisect.bisect_left(cands, e, i + 1)
        else:
            i += 1
    return out, e


_NEG = -(10**9)
_POS = 10**9


def _solve_row(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    yb: int,
    cands: dict[str, list[int]],
    prev: list[Vec2] | None = None,
    hint: float | None = None,
) -> tuple[dict[str, list[int]], list[tuple[int, str]]] | None:
    """Drills and poles for one belt row, maximising drills then fewest poles.

    Dynamic programme over the pole sequence along the row (a pole is a column on one
    side; consecutive poles within wire reach). Between two poles each side is packed
    greedily with the drills that touch one of the two supply areas. The state is the pole
    plus where each side's last drill ended. ``None`` when no wire-connected pole chain
    can serve the candidates (blocked terrain)."""
    w = g.w
    allc = [a for s in SIDES for a in cands[s]]
    if not allc and prev is None:
        return {s: [] for s in SIDES}, []
    spec = g.poles
    pw = spec["north"].width
    reach_pad = max(max(abs(sp.lo), abs(sp.hi)) for sp in spec.values()) + w
    span = list(allc)
    if prev:  # the chain may have to reach out to the poles already placed
        span += [math.floor(q.x) - w for q in prev] + [math.ceil(q.x) + w for q in prev]
    if hint is not None:
        span.append(math.floor(hint))
    cmin, cmax = min(span) - reach_pad, max(span) + w + reach_pad
    ok_cache: dict[tuple[str, int], bool] = {}

    def can_stand(side: str, c: int) -> bool:
        k = (side, c)
        if k not in ok_cache:
            top, _ = g.rows[side]
            cen = footprint.center_for_tile_anchor(
                gd, p.pole, Tile(c, yb + top + spec[side].oy), Direction.N
            )
            ok_cache[k] = terr.can_build_all(footprint.tiles_of(gd, p.pole, cen, Direction.N))
        return ok_cache[k]

    link_cache: dict[tuple[str, int], bool] = {}

    def linked(side: str, c: int) -> bool:
        if prev is None:
            return True
        k = (side, c)
        if k not in link_cache:
            q = Vec2(c + spec[side].xcen, yb + spec[side].ycen)
            link_cache[k] = any(q.distance(o) <= g.wire + _EPS for o in prev)
        return link_cache[k]

    def cover(side: str, c: int) -> tuple[int, int]:
        return c + spec[side].lo, c + spec[side].hi

    def ranges_for(side: str, poles: list[tuple[int, str]]) -> list[tuple[int, int]]:
        return [cover(ps, pc) for pc, ps in poles if side in spec[ps].reach]

    Key = tuple[str, int, int, bool]  # side, where each side's last drill ended, linked
    Entry = tuple[tuple[int, int, int], Any, dict[str, list[int]]]  # value, prev, drills added
    states: dict[int, dict[Key, Entry]] = {}

    def advance(
        before: tuple[int, str, int, int] | None, c2: int, s2: str
    ) -> tuple[dict[str, list[int]], int, int] | None:
        """Pack the segment before a new pole (c2, s2); None if it cannot stand there."""
        if before is None:
            e = {"north": _NEG, "south": _NEG}
            rngs = {s: ranges_for(s, [(c2, s2)]) for s in SIDES}
        else:
            c1, s1, eN, eS = before
            e = {"north": eN, "south": eS}
            rngs = {s: ranges_for(s, [(c1, s1), (c2, s2)]) for s in SIDES}
        added: dict[str, list[int]] = {}
        for s in SIDES:
            a_max = c2 - w if s == s2 else c2 - 1
            added[s], e[s] = _pack(cands[s], w, e[s], a_max, rngs[s])
        if e[s2] > c2:
            return None
        e[s2] = c2 + pw
        return added, e["north"], e["south"]

    def norm(c: int, s: str, eN: int, eS: int, lk: bool) -> Key:
        x0, _ = cover(s, c)
        floor = x0 - w + 1
        return s, max(eN, floor), max(eS, floor), lk

    def near(c: int) -> int:
        return 0 if hint is None else -round(abs(c + 0.5 - hint))

    def put(c: int, key: Key, entry: Entry) -> None:
        cur = states.setdefault(c, {}).get(key)
        if cur is None or entry[0] > cur[0]:
            states[c][key] = entry

    for c in range(cmin, cmax + 1):
        for s in SIDES:
            if not can_stand(s, c):
                continue
            adv = advance(None, c, s)
            if adv is None:
                continue
            added, eN, eS = adv
            n = sum(len(v) for v in added.values())
            put(c, norm(c, s, eN, eS, linked(s, c)), ((n, -1, near(c)), None, added))
    best: tuple[tuple[int, int, int], tuple[int, Key], dict[str, list[int]]] | None = None
    for c in range(cmin, cmax + 1):
        for key, ent in list(states.get(c, {}).items()):
            s, eN, eS, lk = key
            val = ent[0]
            if lk:  # finish here
                tail: dict[str, list[int]] = {}
                e = {"north": eN, "south": eS}
                for sd in SIDES:
                    tail[sd], e[sd] = _pack(cands[sd], w, e[sd], _POS, ranges_for(sd, [(c, s)]))
                total = (val[0] + sum(len(v) for v in tail.values()), val[1], val[2])
                if best is None or total > best[0]:
                    best = (total, (c, key), tail)
            for c2 in range(c + 1, cmax + 1):
                if c2 - c > g.wire + 1 + _EPS:
                    break
                for s2 in SIDES:
                    dy = spec[s2].ycen - spec[s].ycen
                    if math.hypot(c2 - c, dy) > g.wire + _EPS:
                        continue
                    if not can_stand(s2, c2):
                        continue
                    adv = advance((c, s, eN, eS), c2, s2)
                    if adv is None:
                        continue
                    added, n2N, n2S = adv
                    n = sum(len(v) for v in added.values())
                    put(
                        c2,
                        norm(c2, s2, n2N, n2S, lk or linked(s2, c2)),
                        ((val[0] + n, val[1] - 1, val[2] + near(c2)), (c, key), added),
                    )
    if best is None:
        return None
    drills: dict[str, list[int]] = {s: list(best[2][s]) for s in SIDES}
    poles: list[tuple[int, str]] = []
    cur: tuple[int, Key] | None = best[1]
    while cur is not None:
        c, key = cur
        ent = states[c][key]
        poles.append((c, key[0]))
        for s in SIDES:
            drills[s] += ent[2][s]
        cur = ent[1]
    poles.reverse()
    for s in SIDES:
        drills[s].sort()
    return drills, poles


def _free_pack(cands: dict[str, list[int]], w: int) -> dict[str, list[int]]:
    """Packing with no poles: every candidate column allowed."""
    return {s: _pack(cands[s], w, _NEG, _POS, [(_NEG, _POS)])[0] for s in SIDES}


def _connected(gd: GameData, p: MiningPatchParams, poles: list[Placement]) -> bool:
    if len(poles) <= 1:
        return True
    pairs = pole.copper_tree(gd, [(q.name, q.position) for q in poles])
    return len(pairs) == len(poles) - 1


def _build(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    ore: frozenset[Tile],
    py: int,
) -> _Result:
    rows, cands, dropped = _candidates(p, gd, g, terr, ore, py)
    sorted_c: dict[tuple[int, str], list[int]] = {k: sorted(v) for k, v in cands.items()}

    def row_cands(r: int) -> dict[str, list[int]]:
        return {s: sorted_c.get((r, s), []) for s in SIDES}

    free_n = {r: sum(len(v) for v in _free_pack(row_cands(r), g.w).values()) for r in rows}
    keep_rows = set(rows)
    if p.max_belts is not None:
        best = max(
            range(len(rows)),
            key=lambda s: (sum(free_n.get(r, 0) for r in range(s, s + p.max_belts)), -s),
        )
        keep_rows = set(range(best, best + p.max_belts))
        for r in rows:
            if r in keep_rows:
                continue
            for s, packed in _free_pack(row_cands(r), g.w).items():
                for a in packed:
                    dropped.append(_drop(cands[(r, s)][a], "max_belts"))
    used_rows = [r for r in rows if r in keep_rows and free_n[r] > 0]
    if not used_rows:
        return _Result([], [], dropped, 0, [], (py,), (0,))
    # lane capacity: one lane per side, cut from the upstream end
    rate = mining.drill_rate(gd, p.drill, p.resource)
    cap = tp.belt_lane_capacity(gd, p.belt)
    per_lane = math.floor(cap / rate + _EPS)

    def cut_lanes(by_rs: dict[tuple[int, str], list[_Cand]], note: bool) -> list[_Cand]:
        out: list[_Cand] = []
        for r in rows:
            for lane in ("left", "right"):
                cs = sorted(
                    (c for (rr, _), v in by_rs.items() if rr == r for c in v if c.lane == lane),
                    key=lambda c: -c.x,
                )
                out += cs[:per_lane]
                if note:
                    for c in cs[per_lane:]:
                        dropped.append(
                            _drop(
                                c,
                                "lane_capacity",
                                f"{len(cs)} drills x {rate:g}/s > {p.belt} lane {cap:g}/s",
                            )
                        )
        return out

    free_by: dict[tuple[int, str], list[_Cand]] = {}
    for r in used_rows:
        fp = _free_pack(row_cands(r), g.w)
        for s in SIDES:
            free_by[(r, s)] = [cands[(r, s)][a] for a in fp[s]]
    free = cut_lanes(free_by, False)

    def free_x(r: int) -> list[int]:
        return [a for v in _free_pack(row_cands(r), g.w).values() for a in v]

    chosen: dict[tuple[int, str], list[_Cand]] = {}
    poles: list[Placement] = []
    solved: list[Vec2] = []
    for r in range(used_rows[0], used_rows[-1] + 1):
        if r not in keep_rows:
            continue
        # a row with no drills between used rows only carries the power chain; aim it at
        # the next used row's drills
        nxt = [x for u in used_rows if u > r for x in free_x(u)]
        sol = _solve_row(
            p,
            gd,
            g,
            terr,
            rows[r],
            row_cands(r) if r in used_rows else {s: [] for s in SIDES},
            solved or None,
            (sum(nxt) / len(nxt)) if (nxt and r not in used_rows) else None,
        )
        if sol is None:
            return _Result([], [], dropped, -1, [], (py,), (), free)
        drills, pcols = sol
        for s in SIDES:
            chosen[(r, s)] = [cands[(r, s)][a] for a in drills[s]]
        for c, s in pcols:
            top, _ = g.rows[s]
            anchor = Tile(c, rows[r] + top + g.poles[s].oy)
            cen = footprint.center_for_tile_anchor(gd, p.pole, anchor)
            poles.append(Placement(p.pole, cen))
            solved.append(cen)
    # rows in between used rows still need a wire path: handled by the connectivity check
    if not _connected(gd, p, poles):
        return _Result([], [], dropped, -1, [], (py,), (), free)
    final = cut_lanes(chosen, True)
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
    score = (n, -cap_drops, -len(poles), -py)
    return _Result(placements, belts, dropped, n, poles, (py,), score, free, final)


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
        free_best: _Result | None = None
        for py in range(g.period):
            res = _build(p, gd, g, terr, ore, py)
            if free_best is None or len(res.free) > len(free_best.free):
                free_best = res
            if res.drills < 0:
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
        # drills the poles cost: the pole-free packing of the same patch (its best row
        # phase) holds more drills than the powered layout
        assert free_best is not None
        lost = len(free_best.free) - best.drills
        if lost > 0:
            kept = {c.pos for c in best.final}
            gone = sorted(
                (c for c in free_best.free if c.pos not in kept),
                key=lambda c: (c.row, c.side, c.x),
            )
            for c in gone[:lost]:
                best.dropped.append(_drop(c, "pole_slot", "pole-free packing holds one more"))
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
