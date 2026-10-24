"""ASCII rendering: the main human review tool while no server is available.

One character per tile. Belts show their flow direction; multi-tile entities fill all
of their tiles with the same letter. Tiles covered by more than one entity show ``#``;
entities missing from the game data are drawn as ``?`` on their centre tile.

``background`` (tile -> character) fills tiles no entity covers, e.g. ore under a
mining layout (:func:`fgen.terrain.model.background`: ``:`` ore, ``,`` other resource,
``~`` water, ``x`` blocked or occupied).
"""

from __future__ import annotations

from collections.abc import Mapping

from fgen.data.schema import GameData
from fgen.geom import footprint
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Layout, Placement

EMPTY = "."
OVERLAP = "#"
UNKNOWN = "?"

_ARROWS = {Direction.N: "^", Direction.E: ">", Direction.S: "v", Direction.W: "<"}

TYPE_CHARS: dict[str, str] = {
    "inserter": "i",
    "furnace": "F",
    "electric-pole": "P",
    "assembling-machine": "A",
    "mining-drill": "D",
    "pipe": "=",
    "pipe-to-ground": "=",
    "container": "c",
    "underground-belt": "U",
    "splitter": "S",
    "offshore-pump": "O",
    "storage-tank": "T",
}

LEGEND = (
    "legend: ^>v< belt (flow)  U underground  S splitter  i inserter  P pole  F furnace  "
    "A machine  D drill  = pipe  c chest  O offshore pump  T tank  # overlap  ? unknown"
)
TERRAIN_LEGEND = "terrain: : ore (mined resource)  , other resource  ~ water  x blocked/occupied"


def char_for(gd: GameData, p: Placement) -> str:
    e = gd.entities.get(p.name)
    if e is None:
        return UNKNOWN
    if e.type == "transport-belt":
        return _ARROWS.get(p.direction, UNKNOWN)
    return TYPE_CHARS.get(e.type, UNKNOWN)


def to_grid(gd: GameData, layout: Layout) -> dict[Tile, str]:
    grid: dict[Tile, str] = {}
    for p in layout.placements:
        ch = char_for(gd, p)
        if p.name in gd.entities:
            try:
                ts = footprint.tiles_of(gd, p.name, p.position, p.direction)
            except ValueError:
                ts = frozenset({p.position.floor()})
                ch = UNKNOWN
        else:
            ts = frozenset({p.position.floor()})
        for t in ts:
            grid[t] = OVERLAP if t in grid else ch
    return grid


def to_ascii(
    layout: Layout,
    gd: GameData,
    *,
    rulers: bool = False,
    background: Mapping[Tile, str] | None = None,
) -> str:
    """Render ``layout``; ``rulers`` adds x (mod 10) and y coordinate labels;
    ``background`` characters show through where no entity is (and widen the frame)."""
    grid = to_grid(gd, layout)
    if background:
        grid = {**background, **grid}
    if not grid:
        return ""
    x0 = min(t.x for t in grid)
    x1 = max(t.x for t in grid)
    y0 = min(t.y for t in grid)
    y1 = max(t.y for t in grid)
    rows = []
    width = max(len(str(y0)), len(str(y1)))
    if rulers:
        rows.append(" " * (width + 1) + "".join(str(abs(x) % 10) for x in range(x0, x1 + 1)))
    for y in range(y0, y1 + 1):
        line = "".join(grid.get(Tile(x, y), EMPTY) for x in range(x0, x1 + 1))
        rows.append(f"{y:>{width}} {line}" if rulers else line)
    return "\n".join(rows)
