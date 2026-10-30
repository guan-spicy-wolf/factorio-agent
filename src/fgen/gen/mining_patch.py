"""Generator family 2: drills over an ore patch (anchor unit, plan-phase2 §3.2).

Canonical frame (``axis="x"``, ``flow="+"``): belts run east along rows; each belt row
has a row of drills on each side facing it (north side faces S, south side faces N), so
every drill drops onto the belt (the drop tile comes from ``vector_to_place_result``,
the lane from where the drop point lies inside the belt tile: the drill's own side).
With the electric drill and small poles::

    DDDDDDDDDDDDDDD    north drill row (3 tall), packed
    DDDDDDDDDDDDDDD
    DDDDDDDDDDDDDDD
    P>UPU>>>UPU>>>>    belt row: poles stand in the belt, the belt passes under them
    DDDDDDDDDDDDDDD    (U = underground entrance / exit; drills drop onto those too)
    DDDDDDDDDDDDDDD    south drill row
    DDDDDDDDDDDDDDD

Belt rows repeat every ``period`` = drill rows + belt + drill rows (7 for the 3x3
drill). Other axes/flows are the canonical frame rotated (``layout.transform``).

Derived from ``GameData`` (no entity names in the logic; phase 6a):

- drill positions: any column along the belt (no fixed grid). A position may hold a
  drill when its search area (``fgen.geom.mining.search_tiles``) holds at least
  ``min_ore_tiles_per_drill`` patch tiles and ``min_ore_amount_per_drill`` ore, no other
  resource the drill would mine too (unless ``allow_mixed``; reasons ``low_ore`` /
  ``mixed_resource``), and its footprint and belt tile are buildable. Each side of each
  belt row is packed with the most drills (interval scheduling, then most ore tiles);
  the y phase of the rows is searched (``period`` options).
- lane capacity: each lane carries the drills of one side; drills beyond
  ``lane capacity / drill rate`` are cut from the upstream end (reason
  ``lane_capacity``) and ``meta["suggestions"]`` names a belt tier or axis that keeps them.
  ``max_belt_rate`` (e.g. what the consumer of a belt takes) cuts further from the
  upstream end (reason ``belt_rate_cap``).
- poles, after the drills: per belt row, pole spots are the free tiles of the row's
  strip (row ends, gaps in the patch) and, when the pole is one tile deep and the belt
  tier has an underground belt, belt tiles that are no drop tile, with an underground
  pair around the pole inside the belt (entrance and exit no further apart than
  ``max_distance``, exit upstream of the output tile). Only spots whose supply area
  reaches both drill rows are used if there are any (else one chain per drill row). A
  DP picks the cheapest chain (poles, then underground pairs): neighbours within
  ``maximum_wire_distance``, every drill supplied by one of the two poles around it or
  by a pole of an earlier row; a chain wired to the rows above is preferred. Rows still
  out of wire reach (e.g. belt period > wire) are joined with ``route.power.chain``
  over free tiles. Only when no chain exists does a drill give way (reason
  ``pole_slot``): ``meta["drills_before_poles"]`` minus the drills kept.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.geom import belt as beltgeom
from fgen.geom import footprint, mining, pole
from fgen.geom.belt import Lane
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout import transform as tf
from fgen.layout.model import (
    Interface,
    Layout,
    Placement,
    PortGroup,
    PortSpace,
    RateProfile,
    Unit,
)
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
    max_belt_rate: float | None = None  # per belt, e.g. what the consumer takes (None: lanes)


@dataclass
class _Cand:
    row: int  # belt row index
    side: str
    x: int  # drop column (= belt tile x)
    pos: Vec2
    ore: int  # patch tiles in the search area
    lane: Lane
    span: tuple[int, int]  # the drill's columns (inclusive)


@dataclass
class _Geo:
    """Per-parameter geometry in the canonical frame (independent of the patch)."""

    w: int  # drill width along the belt
    period: int
    rows: dict[str, tuple[int, int]]  # side -> drill top-row offset from belt row, height
    anchor_dx: dict[str, int]  # side -> drill anchor x - drop column
    lanes: dict[str, Lane]
    strip: tuple[int, int]  # first / last row offset of one belt row's strip
    underground: str | None  # underground belt of the belt's tier (poles in the belt row)
    groups: tuple[tuple[str, ...], ...]  # drill rows served by one pole chain each


@dataclass(frozen=True)
class _Site:
    """A spot for one pole of a belt row (canonical frame)."""

    pos: Vec2
    tiles: frozenset[Tile]
    ug: tuple[Tile, Tile] | None  # entrance / exit tiles when the pole sits in the belt row
    cover: int  # bit mask over the row's drills (sorted by drop column)


@dataclass
class _Result:
    placements: list[Placement]
    belts: list[dict[str, Any]]
    dropped: list[dict[str, Any]]
    drills: int
    poles: list[Placement]
    phase: tuple[int, int]
    score: tuple[Any, ...] = field(default=())
    undergrounds: int = 0
    free_drills: int = 0  # drills the rows hold before poles take any


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
    if p.max_belt_rate is not None and p.max_belt_rate <= 0:
        raise Infeasible("max_belt_rate must be > 0 or None", ["max_belt_rate=None"])
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
    from fgen.route.belt import underground_for

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
    strip = (n_top, s_top + s_h - 1)
    # one chain per belt row when a pole between the drills (in a drill row, or in the
    # belt row over an underground pair when it is one tile deep) supplies both drill
    # rows, else one chain per drill row
    ug = underground_for(gd, p.belt)
    _, ph = footprint.size(gd, p.pole)

    def inside(oy: int) -> bool:
        ys = set(range(oy, oy + ph))
        return 0 not in ys or (ys == {0} and ug is not None)

    both = any(
        inside(oy) and _reaches(p, gd, rows, oy) == set(SIDES)
        for oy in range(strip[0], strip[1] - ph + 2)
    )
    groups = (SIDES,) if both else tuple((s,) for s in SIDES)
    return _Geo(w, period, rows, anchor_dx, lanes, strip, ug, groups)


def _reaches(
    p: MiningPatchParams, gd: GameData, rows: dict[str, tuple[int, int]], oy: int
) -> set[str]:
    """Drill rows a pole anchored ``oy`` rows from the belt supplies (some tile of)."""
    c = footprint.center_for_tile_anchor(gd, p.pole, Tile(0, oy))
    _, y0, _, y1 = pole.supply_range(gd, p.pole, c)
    return {s for s, (top, h) in rows.items() if y0 <= top + h - 1 and y1 >= top}


def _drill_anchor(g: _Geo, side: str, x: int, yb: int) -> Tile:
    return Tile(x + g.anchor_dx[side], yb + g.rows[side][0])


def _candidates(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    ore: frozenset[Tile],
    py: int,
) -> tuple[dict[int, int], list[_Cand], list[tuple[_Cand, str, str]]]:
    """Belt rows (index -> y), every drill position that may hold a drill (any column:
    drills are not on a fixed grid along the belt) and rejected positions with reason."""
    lo, hi = bounds_of(ore)
    ox0, oy0, ox1, oy1 = lo.x, lo.y, hi.x, hi.y
    r = gd.entity(p.drill).resource_searching_radius or 0
    margin = math.ceil(r) + g.w + g.period
    ys = [y for y in range(oy0 - margin, oy1 + margin + 1) if (y - py) % g.period == 0]
    xs = range(ox0 - margin, ox1 + margin + 1)
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
    rejected: list[tuple[_Cand, str, str]] = []
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
                c = _Cand(i, side, x, pos, n, g.lanes[side], (a.x, a.x + g.w - 1))
                if amount(*box) < p.min_ore_amount_per_drill:
                    rejected.append((c, "low_ore", f"{amount(*box)} ore in its area"))
                elif mixed is not None and not p.allow_mixed and mixed(*box):
                    rejected.append((c, "mixed_resource", ""))
                else:
                    cands.append(c)
    # a belt must run from each drill to the row's downstream end
    kept: list[_Cand] = []
    for i, yb in rows.items():
        cs = [c for c in cands if c.row == i]
        if not cs:
            continue
        end = max(c.x for c in cs)
        lo_x = min(c.x for c in cs)
        blocked = [x for x in range(lo_x, end + 1) if not terr.can_build(Tile(x, yb))]
        cut = max(blocked) if blocked else None
        for c in cs:
            if cut is not None and c.x < cut:
                rejected.append((c, "belt_blocked", f"belt blocked at ({cut},{yb})"))
            else:
                kept.append(c)
    return rows, kept, rejected


def _pack(cands: list[_Cand]) -> list[_Cand]:
    """Most drills with disjoint columns (then most ore tiles): interval scheduling."""
    cs = sorted(cands, key=lambda c: c.span)
    starts = [c.span[0] for c in cs]
    best: list[tuple[int, int, int]] = [(0, 0, -1)] * (len(cs) + 1)  # (n, ore, next)
    for i in range(len(cs) - 1, -1, -1):
        j = bisect.bisect_right(starts, cs[i].span[1])
        take = (best[j][0] + 1, best[j][1] + cs[i].ore, j)
        skip = (best[i + 1][0], best[i + 1][1], -2)
        best[i] = take if take[:2] > skip[:2] else skip
    out: list[_Cand] = []
    i = 0
    while i < len(cs):
        n = best[i][2]
        if n == -2:
            i += 1
        else:
            out.append(cs[i])
            i = n
    return out


def _report(rejected: list[tuple[_Cand, str, str]], placed: list[_Cand]) -> list[dict[str, Any]]:
    """One drop entry per rejected spot that a drill could have taken: rejected
    positions packed left to right around the placed drills of the same row side."""
    taken: dict[tuple[int, str], list[tuple[int, int]]] = {}
    for c in placed:
        taken.setdefault((c.row, c.side), []).append(c.span)
    out: list[dict[str, Any]] = []
    for c, reason, detail in sorted(rejected, key=lambda r: (r[0].row, r[0].side, r[0].span)):
        spans = taken.setdefault((c.row, c.side), [])
        if any(a <= c.span[1] and c.span[0] <= b for a, b in spans):
            continue
        spans.append(c.span)
        out.append(_drop(c, reason, detail))
    return out


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


def _drill_tiles(gd: GameData, p: MiningPatchParams, c: _Cand) -> frozenset[Tile]:
    return footprint.tiles_of(gd, p.drill, c.pos, FACING[c.side])


def _row_sites(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    yb: int,
    drills: list[_Cand],
    group: tuple[str, ...],
) -> list[_Site]:
    """Pole spots in one belt row's strip: any buildable spot free of drills, and in
    the belt itself when the pole is one tile deep and an underground pair of the
    belt's tier can pass under it (entrance and exit inside the belt, exit upstream of
    the output tile, pole tiles not under a drop point). No spot downstream of the
    output (the belt leaves the unit there). Only spots whose supply reaches every drill
    row of ``group`` are kept; ``cover`` is over the drills of ``group``."""
    x0, x1 = min(c.x for c in drills), max(c.x for c in drills)
    occupied: set[Tile] = set()
    for c in drills:
        occupied |= _drill_tiles(gd, p, c)
    drops = {Tile(c.x, yb) for c in drills}
    pw, ph = footprint.size(gd, p.pole)
    sad = gd.entity(p.pole).supply_area_distance or 0.0
    lo_x = min(c.span[0] for c in drills) - math.ceil(sad) - pw
    top, bottom = yb + g.strip[0], yb + g.strip[1]
    bands = {s: (yb + g.rows[s][0], yb + g.rows[s][0] + g.rows[s][1] - 1) for s in SIDES}
    reach_max = int(gd.entity(g.underground).max_distance or 0) if g.underground else 0
    targets = sorted((c for c in drills if c.side in group), key=lambda c: c.x)
    ys = [y for y in range(top, bottom - ph + 2) if set(group) <= _reaches(p, gd, g.rows, y - yb)]
    # pole footprint, centre and supply rectangle relative to its anchor tile
    c0 = footprint.center_for_tile_anchor(gd, p.pole, Tile(0, 0))
    offs = [t for t in footprint.tiles_of(gd, p.pole, c0, Direction.N)]
    r0 = pole.supply_range(gd, p.pole, c0)
    out: list[_Site] = []
    for ay in ys:
        for ax in range(lo_x, x1 + 1 - (pw - 1)):
            ts = frozenset(Tile(ax + t.x, ay + t.y) for t in offs)
            if ts & occupied or not terr.can_build_all(ts):
                continue
            ug = None
            if any(t.y == yb and x0 <= t.x <= x1 for t in ts):
                if g.underground is None or any(t.y != yb for t in ts) or ts & drops:
                    continue
                a = Tile(min(t.x for t in ts) - 1, yb)
                b = Tile(max(t.x for t in ts) + 1, yb)
                if a.x < x0 or b.x >= x1 or b.x - a.x > reach_max:
                    continue
                ug = (a, b)
            sx0, sy0, sx1, sy1 = r0[0] + ax, r0[1] + ay, r0[2] + ax, r0[3] + ay
            mask = 0
            for k, d in enumerate(targets):
                (dx0, dx1), (dy0, dy1) = d.span, bands[d.side]
                if dx0 <= sx1 and dx1 >= sx0 and dy0 <= sy1 and dy1 >= sy0:
                    mask |= 1 << k
            out.append(_Site(Vec2(c0.x + ax, c0.y + ay), ts, ug, mask))
    out.sort(key=lambda s: (s.pos.x, s.pos.y))
    return out


def _chain(
    p: MiningPatchParams,
    gd: GameData,
    sites: list[_Site],
    centres: list[float],
    links: list[Vec2],
    pre: int = 0,
) -> tuple[list[_Site] | None, int]:
    """Cheapest pole chain along one belt row: consecutive poles in wire reach, every
    drill (bit k = k-th drill by drop column, centre ``centres[k]``) covered by one of
    the two poles around it (or the first / last pole beyond the ends); poles in the belt
    cost their underground pair. Prefers a chain with a pole in wire reach of ``links``
    (the poles of rows already placed); drills in ``pre`` are supplied already. Returns
    (chain, -1) or (None, k): no chain covers drill k (the first drill no feasible prefix
    reaches)."""
    n = len(centres)
    full = (1 << n) - 1
    if pre & full == full:
        return [], -1
    sites = [replace(s, cover=s.cover | pre) for s in sites]
    wire = pole.wire_distance(gd, p.pole, p.pole)
    reach2 = (wire + _EPS) ** 2
    xs = [s.pos.x for s in sites]
    ys = [s.pos.y for s in sites]
    if not links:
        links_ok = [True] * len(sites)
    else:
        links_ok = [
            any((q.x - x) ** 2 + (q.y - y) ** 2 <= reach2 for q in links)
            for x, y in zip(xs, ys, strict=True)
        ]
    below = [(1 << bisect.bisect_left(centres, x - _EPS)) - 1 for x in xs]  # centre < x
    upto = [(1 << bisect.bisect_right(centres, x + _EPS)) - 1 for x in xs]  # centre <= x
    ugt = [frozenset(s.ug) if s.ug else frozenset() for s in sites]
    # state (site, linked) -> (cost, prev state, last underground exit x)
    best: dict[tuple[int, bool], tuple[tuple[int, int], tuple[int, bool] | None, float]] = {}
    for i, s in enumerate(sites):
        if below[i] & ~s.cover:
            continue
        cost = (1, 1 if s.ug else 0)
        key = (i, links_ok[i])
        exit_x = s.ug[1].x if s.ug else -math.inf
        if key not in best or cost < best[key][0]:
            best[key] = (cost, None, exit_x)
    # sites are sorted by x: relax in order
    for i, s in enumerate(sites):
        for linked in (False, True):
            cur = best.get((i, linked))
            if cur is None:
                continue
            for j in range(i + 1, len(sites)):
                dx = xs[j] - xs[i]
                if dx > wire + _EPS:
                    break
                if dx * dx + (ys[j] - ys[i]) ** 2 > reach2:
                    continue
                t = sites[j]
                if t.ug and (t.ug[0].x <= cur[2] or (s.ug and t.ug[0].x <= s.ug[1].x)):
                    continue
                if s.tiles & t.tiles or t.tiles & ugt[i] or s.tiles & ugt[j]:
                    continue
                if (upto[j] & ~below[i]) & ~(s.cover | t.cover):
                    continue
                cost = (cur[0][0] + 1, cur[0][1] + (1 if t.ug else 0))
                key = (j, linked or links_ok[j])
                exit_x = t.ug[1].x if t.ug else cur[2]
                if key not in best or cost < best[key][0]:
                    best[key] = (cost, (i, linked), exit_x)
    finals = [
        (not k[1], v[0], k)
        for k, v in best.items()
        if not (full & ~upto[k[0]] & ~sites[k[0]].cover)
    ]
    if finals:
        _, _, k = min(finals)
        out: list[_Site] = []
        cur_k: tuple[int, bool] | None = k
        while cur_k is not None:
            out.append(sites[cur_k[0]])
            cur_k = best[cur_k][1]
        return out[::-1], -1
    # first drill no reached state covers
    progress = 0
    for (i, _), _v in best.items():
        covered = upto[i] | sites[i].cover
        k = 0
        while k < n and covered >> k & 1:
            k += 1
        progress = max(progress, k)
    return None, min(progress, n - 1)


def _row_poles(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    yb: int,
    cs: list[_Cand],
    links: list[Vec2],
) -> tuple[list[_Cand], list[_Site], list[_Cand]]:
    """Pole chains of one belt row (one per ``g.groups`` entry); where no chain exists a
    drill makes room (of the drill no chain reaches and the one before it, the removal
    that lets a chain reach furthest). Returns (drills kept, poles, drills removed)."""
    removed: list[_Cand] = []

    def attempt(drills: list[_Cand], grp: tuple[str, ...], ln: list[Vec2]):
        order = sorted((c for c in drills if c.side in grp), key=lambda c: c.x)
        if not order:
            return [], -1, order
        sites = _row_sites(p, gd, g, terr, yb, drills, grp)
        supplied: set[Tile] = set()
        for q in ln:
            supplied |= pole.supply_tiles(gd, p.pole, q)
        pre = sum(1 << k for k, c in enumerate(order) if _drill_tiles(gd, p, c) & supplied)
        ch, k = _chain(p, gd, sites, [c.pos.x for c in order], ln, pre)
        return ch, k, order

    while True:
        chains: list[_Site] = []
        restart = False
        for grp in g.groups:
            while True:
                ch, k, order = attempt(cs, grp, links + [s.pos for s in chains])
                if ch is not None:
                    chains += ch
                    break
                options = [order[k]] + ([order[k - 1]] if k > 0 else [])
                best_c, best_k = options[0], -2
                for c in options:
                    rest = [d for d in cs if d is not c]
                    ch2, k2, _ = attempt(rest, grp, links + [s.pos for s in chains])
                    score = len(order) if ch2 is not None else k2
                    if score > best_k:
                        best_c, best_k = c, score
                cs = [d for d in cs if d is not best_c]
                removed.append(best_c)
                restart = len(g.groups) > 1
            if restart:
                break
        if not restart:
            return cs, chains, removed


def _bridge(
    p: MiningPatchParams,
    gd: GameData,
    terr: Terrain,
    placements: list[Placement],
    poles: list[Placement],
    keep_clear: set[Tile],
) -> list[Placement] | None:
    """Join pole networks that are out of wire reach (e.g. belt rows further apart than
    the wire) with pole chains over free tiles (``route.power.chain``)."""
    from fgen.route.belt import RouteError
    from fgen.route.grid import RouteGrid
    from fgen.route.power import chain

    def components(ps: list[Placement]) -> list[list[int]]:
        seen: set[int] = set()
        out = []
        for a in range(len(ps)):
            if a in seen:
                continue
            comp, stack = [], [a]
            seen.add(a)
            while stack:
                u = stack.pop()
                comp.append(u)
                for v in range(len(ps)):
                    if v not in seen and pole.wire_reaches(
                        gd, ps[u].name, ps[u].position, ps[v].name, ps[v].position
                    ):
                        seen.add(v)
                        stack.append(v)
            out.append(comp)
        return out

    added: list[Placement] = []
    grid = RouteGrid.build(gd, terr, [("unit", Layout(placements + poles), {})])
    grid.blocked |= keep_clear
    while True:
        ps = poles + added
        comps = components(ps)
        if len(comps) == 1:
            return added
        main, rest = comps[0], [q for c in comps[1:] for q in c]

        # drill rows are packed between their ends: try the poles at the upstream ends
        # (free ground there by construction) first, then the closest pairs
        pos = [q.position for q in ps]
        pairs = [(a, b) for a in main for b in rest]
        near = sorted(pairs, key=lambda ab: pos[ab[0]].distance(pos[ab[1]]))
        pairs.sort(
            key=lambda ab: (max(pos[ab[0]].x, pos[ab[1]].x), pos[ab[0]].distance(pos[ab[1]]))
        )
        found = None
        for a, b in pairs[:3] + near[:2]:
            try:
                found = chain(
                    grid, p.pole, (p.pole, ps[a].position), (p.pole, ps[b].position), "bridge"
                )
                break
            except RouteError:
                continue
        if found is None:
            return None
        grid.add(found, "bridge")
        added += found


@dataclass
class _Power:
    drills: list[_Cand]
    poles: list[Placement]
    ugs: dict[Tile, str]  # belt tile -> underground type
    pole_tiles: set[Tile]
    keep_clear: set[Tile]  # belt-row tiles downstream of the outputs
    removed: list[_Cand]


def _power(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    rows: dict[int, int],
    drills: list[_Cand],
) -> _Power:
    """Poles row by row (top to bottom), each chain linked to the rows above when it can."""
    out = _Power([], [], {}, set(), set(), [])
    _, hi_all = terr.bounds()
    for r, yb in rows.items():
        cs, chain_, removed = _row_poles(
            p, gd, g, terr, yb, [c for c in drills if c.row == r], [q.position for q in out.poles]
        )
        out.removed += removed
        if not cs:
            continue
        out.drills += cs
        for s in chain_:
            out.poles.append(Placement(p.pole, s.pos))
            out.pole_tiles |= s.tiles
            if s.ug:
                out.ugs[s.ug[0]] = "input"
                out.ugs[s.ug[1]] = "output"
        x1 = max(c.x for c in cs)
        out.keep_clear |= {Tile(x, yb) for x in range(x1 + 1, hi_all.x + 1)}
    return out


@dataclass
class _Prep:
    """A y phase before poles: belt rows, packed drills, drops so far."""

    py: int
    rows: dict[int, int]
    packed: list[_Cand]
    dropped: list[dict[str, Any]]
    free: int = 0  # drills after the lane cut, before poles (an upper bound)


def _prepare(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    ore: frozenset[Tile],
    py: int,
) -> _Prep:
    rows, cands, rejected = _candidates(p, gd, g, terr, ore, py)
    packed: list[_Cand] = []
    for r in rows:
        for side in SIDES:
            packed += _pack([c for c in cands if c.row == r and c.side == side])
    dropped = _report(rejected, packed)
    if p.max_belts is not None:
        counts = {r: sum(1 for c in packed if c.row == r) for r in rows}
        best = max(
            range(len(rows)),
            key=lambda s: (sum(counts.get(r, 0) for r in range(s, s + p.max_belts)), -s),
        )
        keep_rows = set(range(best, best + p.max_belts))
        for c in packed:
            if c.row not in keep_rows:
                dropped.append(_drop(c, "max_belts"))
        packed = [c for c in packed if c.row in keep_rows]
    prep = _Prep(py, rows, packed, dropped)
    prep.free = len(_lane_cut(p, gd, rows, packed)[0])
    return prep


def _lane_cut(
    p: MiningPatchParams, gd: GameData, rows: dict[int, int], drills: list[_Cand]
) -> tuple[list[_Cand], list[dict[str, Any]]]:
    """One lane per side, cut from the upstream end."""
    rate = mining.drill_rate(gd, p.drill, p.resource)
    cap = tp.belt_lane_capacity(gd, p.belt)
    per_lane = math.floor(cap / rate + _EPS)
    kept: list[_Cand] = []
    out: list[dict[str, Any]] = []
    per_belt = math.floor(p.max_belt_rate / rate + _EPS) if p.max_belt_rate is not None else None
    for r in rows:
        row: list[_Cand] = []
        for lane in ("left", "right"):
            cs = sorted((c for c in drills if c.row == r and c.lane == lane), key=lambda c: -c.x)
            row += cs[:per_lane]
            detail = f"{len(cs)} drills x {rate:g}/s > {p.belt} lane {cap:g}/s"
            out += [_drop(c, "lane_capacity", detail) for c in cs[per_lane:]]
        if per_belt is not None and len(row) > per_belt:
            row.sort(key=lambda c: -c.x)
            detail = f"{len(row)} drills x {rate:g}/s > max_belt_rate {p.max_belt_rate:g}/s"
            out += [_drop(c, "belt_rate_cap", detail) for c in row[per_belt:]]
            row = row[:per_belt]
        kept += row
    return kept, out


def _build(
    p: MiningPatchParams,
    gd: GameData,
    g: _Geo,
    terr: Terrain,
    prep: _Prep,
) -> _Result | None:
    rows, packed, py = prep.rows, prep.packed, prep.py
    dropped = list(prep.dropped)
    if not packed:
        return _Result([], [], dropped, 0, [], (0, py), (0,))
    rate = mining.drill_rate(gd, p.drill, p.resource)

    def lane_cut(drills: list[_Cand]) -> tuple[list[_Cand], list[dict[str, Any]]]:
        return _lane_cut(p, gd, rows, drills)

    # lane cut first (poles only for drills that stay); when poles then take drills,
    # also try poles first and the lane cut after (drills a pole displaced may be ones
    # the lane would have cut anyway) and keep the better
    kept, cut = lane_cut(packed)
    free_drills = len(kept)  # what the belts would carry if poles took no room
    plan = _power(p, gd, g, terr, rows, kept)
    if plan.removed and cut:
        first = _power(p, gd, g, terr, rows, packed)
        kept2, cut2 = lane_cut(first.drills)
        plan2 = _power(p, gd, g, terr, rows, kept2)
        plan2.removed = first.removed + plan2.removed
        if (len(plan2.drills), -len(plan2.poles)) > (len(plan.drills), -len(plan.poles)):
            # displaced drills beyond what the lanes carry anyway are lane-capacity drops:
            # only free_drills - drills kept count as lost to poles
            surplus = {id(c) for c in packed} - {id(c) for c in kept}
            order = sorted(plan2.removed, key=lambda c: id(c) in surplus)
            lost = max(0, free_drills - len(plan2.drills))
            moved, plan2.removed = order[lost:], order[:lost]
            detail = "displaced by a pole; beyond the lane capacity anyway"
            plan, cut = plan2, cut2 + [_drop(c, "lane_capacity", detail) for c in moved]
    dropped += cut + [_drop(c, "pole_slot") for c in plan.removed]
    final, poles, ugs, pole_tiles, keep_clear = (
        plan.drills,
        plan.poles,
        plan.ugs,
        plan.pole_tiles,
        plan.keep_clear,
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
            t = Tile(x, yb)
            if t in pole_tiles:
                continue
            if t in ugs:
                assert g.underground is not None
                placements.append(Placement(g.underground, t.center(), FLOW, {"type": ugs[t]}))
                continue
            placements.append(
                Placement(p.belt, footprint.center_for_tile_anchor(gd, p.belt, t, FLOW), FLOW)
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
    bridge = _bridge(p, gd, terr, placements, poles, keep_clear) if len(poles) > 1 else []
    if bridge is None:
        return None
    poles += bridge
    placements += poles
    n = len(final)
    cap_drops = sum(1 for d in dropped if d["reason"] == "lane_capacity")
    score = (n, -cap_drops, -len(poles), -len(ugs), -py)
    return _Result(placements, belts, dropped, n, poles, (0, py), score, len(ugs), free_drills)


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
        # phases by their pole-free drill count: none can end with more than that, so
        # stop once a phase reaches the next one's bound
        preps = sorted(
            (_prepare(p, gd, g, terr, ore, py) for py in range(g.period)),
            key=lambda q: (-q.free, q.py),
        )
        for prep in preps:
            if best is not None and prep.free <= best.drills:
                break
            res = _build(p, gd, g, terr, prep)
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
        return replace(unit, meta=meta)

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
        groups: list[PortGroup] = []
        for k, b in enumerate(res.belts):
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
                    rate_profile=RateProfile.fixed(
                        b["rate"], {ln: n * rate for ln, n in b["lanes"].items() if n}
                    ),
                    entity=p.belt,
                    port_group=f"belt{k}",
                    space=PortSpace.item("direct"),
                )
            )
            groups.append(PortGroup(f"belt{k}"))
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
                    entity=p.pole,
                )
            )
        unit = Unit(Layout(res.placements), ifaces, {}, tuple(groups))
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
        lengths = [b["x"][1] - b["x"][0] + 1 for b in res.belts]  # output k = belt k
        dry_by_tile: dict[Tile, float] = {}
        for pl, a in zip(drills, areas, strict=True):
            if a:
                dry_by_tile[mining.drop_tile(gd, p.drill, pl.position, pl.direction)] = sum(
                    patch.amounts.get(t, 0) for t in a
                ) / (rate * max(sharers[t] for t in a))
        drops = {mining.drop_tile(gd, p.drill, pl.position, pl.direction) for pl in drills}
        # belt-like tiles only: a pole slot on the belt is crossed underground and holds nothing
        on_belt = {
            pl.position.floor()
            for pl in unit.layout.placements
            if gd.entity(pl.name).type in ("transport-belt", "underground-belt")
        }
        new_ifaces = []
        for k, i in enumerate(unit.interfaces):
            if i.kind == "item" and i.role == "output" and i.rate is not None:
                bound = None
                first_drop = 0  # belt tiles from the output back to the farthest drill drop
                run = 0
                for s in range(lengths[k]):  # the belt's tiles, upstream from the output
                    t = i.tile.neighbor(i.direction.opposite, s)
                    run += t in on_belt
                    if t in drops:
                        first_drop = run
                    if t in dry_by_tile:
                        v = dry_by_tile[t]
                        bound = v if bound is None else min(bound, v)
                assert i.rate_profile is not None
                i = replace(
                    i,
                    rate_profile=replace(i.rate_profile, depletion_s_lower_bound=bound),
                    # the belt holds items from its first drop to the port
                    buffer_items=beltgeom.run_items(gd, first_drop, i.lane),
                )
            new_ifaces.append(i)
        unit = replace(unit, interfaces=new_ifaces)
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
            "drill_stacks": gd.entity(p.drill).drops_full_belt_stacks,
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
            "undergrounds": res.undergrounds,
            "drills_before_poles": res.free_drills,
            "phase": list(res.phase),
            "dropped_drills": dropped,
            "dropped_by_reason": reasons,
            "suggestions": [],
            "suggestion_changes": [],
        }
        return replace(unit, meta=meta)

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
