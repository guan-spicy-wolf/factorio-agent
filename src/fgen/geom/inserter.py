"""Inserter pickup / drop tiles.

``pickup_position`` / ``insert_position`` are vectors for the N-facing inserter; they
are rotated with the entity, added to the centre and floored. With the seed values an
N-facing (direction 0) inserter picks up from the tile to its north and drops to the
south; an E-facing one picks up from the east. Checked in game on 2.0.76 (items moved
chest to chest, and ``LuaEntity.pickup_position``/``drop_position``): ``fgen verify`` #1.
"""

from __future__ import annotations

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile, Vec2


def _vector_tile(gd: GameData, name: str, pos: Vec2, d: Direction, field: str) -> Tile:
    e = gd.entity(name)
    v = getattr(e, field)
    if v is None:
        raise ValueError(f"{name} has no {field}")
    return (pos + Vec2.of(v).rotate(d)).floor()


def pickup_tile(gd: GameData, name: str, pos: Vec2, direction: Direction) -> Tile:
    return _vector_tile(gd, name, pos, direction, "pickup_position")


def drop_tile(gd: GameData, name: str, pos: Vec2, direction: Direction) -> Tile:
    return _vector_tile(gd, name, pos, direction, "insert_position")


def reach_offsets(gd: GameData, name: str) -> tuple[Tile, Tile]:
    """(pickup, drop) tile offsets relative to the inserter's own tile, facing N."""
    own = Tile(0, 0)
    c = own.center()
    return pickup_tile(gd, name, c, Direction.N) - own, drop_tile(gd, name, c, Direction.N) - own
