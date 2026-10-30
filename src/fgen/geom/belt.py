"""Belt helpers: flow, capacity, lanes, underground range.

Lanes are named relative to the flow direction: ``left`` is
``defines.transport_line.left_line`` (1), the north lane of an east-flowing belt. An
inserter dropping onto a belt from the side places items on the far lane. Both checked in
game on 2.0.76 (``fgen verify`` #4).
"""

from __future__ import annotations

from typing import Literal

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile

type Lane = Literal["left", "right"]

BELT_TYPES = frozenset({"transport-belt", "underground-belt", "splitter"})


def next_tile(tile: Tile, direction: Direction) -> Tile:
    return tile.neighbor(direction)


def items_per_second(gd: GameData, name: str) -> float:
    """Full-belt throughput, both lanes: ``speed * ticks_per_second * belt_items_per_tile``."""
    e = gd.entity(name)
    if e.speed is None or e.type not in BELT_TYPES:
        raise ValueError(f"{name} is not a belt-like entity")
    return e.speed * gd.constant("ticks_per_second") * gd.constant("belt_items_per_tile")


def lane_items_per_second(gd: GameData, name: str) -> float:
    return items_per_second(gd, name) / gd.constant("belt_lanes")


def lane_share(gd: GameData, lane: str | None) -> float:
    """Share of a belt's capacity that lies on the lanes a port with ``lane`` uses: one
    lane of ``belt_lanes`` for ``left`` / ``right``, the whole belt otherwise."""
    return 1.0 / gd.constant("belt_lanes") if lane in ("left", "right") else 1.0


def run_items(gd: GameData, tiles: int, lane: str | None) -> float:
    """Items a run of ``tiles`` belt tiles (underground ends count as tiles, the gap
    between them holds nothing) holds when full on the lanes ``lane`` uses."""
    return tiles * gd.constant("belt_items_per_tile") * lane_share(gd, lane)


def underground_partner_range(gd: GameData, name: str) -> int:
    """Largest entrance-to-exit distance (in tiles) for an underground belt pair."""
    e = gd.entity(name)
    if e.max_distance is None:
        raise ValueError(f"{name} is not an underground belt")
    return int(e.max_distance)


def lane_side(flow: Direction, lane: Lane) -> Direction:
    """Which world direction the given lane lies on, relative to the belt's flow."""
    return flow.rotate_ccw() if lane == "left" else flow.rotate_cw()


def drop_lane(flow: Direction, approach: Direction) -> Lane:
    """Lane an inserter drops onto when its arm moves in ``approach`` onto the belt.

    The far lane is the one on the side ``approach`` points to.
    """
    if approach == flow.rotate_ccw():
        return "left"
    if approach == flow.rotate_cw():
        return "right"
    raise ValueError("drop lane is only defined for side-loading inserters")
