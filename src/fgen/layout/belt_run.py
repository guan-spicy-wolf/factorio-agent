"""The belt run behind an item port, read off a unit's layout (phase 8, ``buffer_items``).

``validate`` uses it to check ``Interface.buffer_items``; a generator whose belts are not
regular enough to count by formula may use it too. The run counts belt-like tiles of the
unit's own layout (a belt, an underground entrance or exit; the gap between a pair holds
nothing):

* input port: every tile reachable from the port tile by following the flow (through
  underground pairs, onto a belt the flow runs into), i.e. up to the end of the belt;
* output port: every tile that flows into the port tile, but only from the first tile
  that receives items (an inserter's or a drill's drop tile) on; belts upstream of the
  first drop stay empty. A belt none of whose tiles is a drop tile (fed by a loader) is
  taken whole.
"""

from __future__ import annotations

from fgen.data.schema import GameData
from fgen.geom import inserter, mining
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Layout, Placement, Role

_BELT_TYPES = ("transport-belt", "underground-belt")


def _kind(p: Placement) -> str:
    return str(p.extra.get("type", "input"))


def _belts(layout: Layout, gd: GameData) -> dict[Tile, Placement]:
    out: dict[Tile, Placement] = {}
    for p in layout.placements:
        if p.name in gd.entities and gd.entity(p.name).type in _BELT_TYPES:
            out[p.position.floor()] = p
    return out


def _accepts(gd: GameData, belts: dict[Tile, Placement], t: Tile, flow: Direction) -> bool:
    q = belts.get(t)
    if q is None:
        return False
    if gd.entity(q.name).type == "transport-belt":
        return q.direction != flow.opposite
    return _kind(q) == "input" and q.direction == flow


def successors(layout: Layout, gd: GameData) -> dict[Tile, Tile]:
    """Next belt tile of every belt-like tile that leads somewhere in the layout."""
    belts = _belts(layout, gd)
    out: dict[Tile, Tile] = {}
    for t, p in belts.items():
        if not p.direction.is_cardinal:
            continue
        if gd.entity(p.name).type == "underground-belt" and _kind(p) == "input":
            reach = int(gd.entity(p.name).max_distance or 0)
            for k in range(1, reach + 1):
                u = t.neighbor(p.direction, k)
                q = belts.get(u)
                if q is not None and q.name == p.name and q.direction == p.direction:
                    if _kind(q) == "output":
                        out[t] = u
                    break
        else:
            n = t.neighbor(p.direction)
            if _accepts(gd, belts, n, p.direction):
                out[t] = n
    return out


def _drop_tiles(layout: Layout, gd: GameData) -> set[Tile]:
    out: set[Tile] = set()
    for p in layout.placements:
        if p.name not in gd.entities or not p.direction.is_cardinal:
            continue
        e = gd.entity(p.name)
        if e.type == "inserter":
            out.add(inserter.drop_tile(gd, p.name, p.position, p.direction))
        elif (
            e.type == "mining-drill"
            and e.vector_to_place_result is not None
            and e.output_fluid_box is None
        ):
            out.add(mining.drop_tile(gd, p.name, p.position, p.direction))
    return out


def run_tiles(layout: Layout, gd: GameData, tile: Tile, role: Role) -> int:
    """Belt tiles in the run of the item port at ``tile`` (0 when no belt stands there)."""
    belts = _belts(layout, gd)
    if tile not in belts:
        return 0
    nxt = successors(layout, gd)
    if role == "input":
        seen, stack = {tile}, [tile]
        while stack:
            t = stack.pop()
            n = nxt.get(t)
            if n is not None and n not in seen:
                seen.add(n)
                stack.append(n)
        return len(seen)
    before: dict[Tile, list[Tile]] = {}
    for a, b in nxt.items():
        before.setdefault(b, []).append(a)
    flowing = {tile}
    stack = [tile]
    while stack:
        t = stack.pop()
        for a in before.get(t, []):
            if a not in flowing:
                flowing.add(a)
                stack.append(a)
    starts = flowing & _drop_tiles(layout, gd)
    if not starts:
        return len(flowing)
    run = set(starts)
    stack = list(starts)
    while stack:
        t = stack.pop()
        n = nxt.get(t)
        if n is not None and n in flowing and n not in run:
            run.add(n)
            stack.append(n)
    return len(run)
