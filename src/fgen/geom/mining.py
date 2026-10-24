"""Mining drill geometry and rates.

Facts measured on 2.1.23 (``docs/VERIFY.md`` §9):

- **Search area**: a resource tile counts when its centre lies in the square of
  half-side ``resource_searching_radius`` around the drill centre. For the electric
  drill (2.49) that is the 5x5 tiles at offsets -2..2 from the drill's centre tile;
  offset 3 is out (the square's edge 2.99 tiles from the centre tile's left edge never
  reaches tile 3). Big drill (6.49): offsets -6..6, 13x13.
- **Output**: the drill puts its product at ``centre + vector_to_place_result``
  (rotated with the drill), i.e. into the tile containing that point; on a belt it
  lands on the lane on the drill's side of the belt (the point lies 0.35 tile from the
  belt tile's edge: the near half).
- Rate without research: ``mining_speed / mining_time`` mining cycles per second, each
  yielding the resource's ``minable`` result amount.
"""

from __future__ import annotations

import math

from fgen.data.schema import GameData
from fgen.geom.belt import Lane
from fgen.geom.vec import Direction, Tile, Vec2

_EPS = 1e-6


def _drill(gd: GameData, name: str):  # -> EntityProto
    e = gd.entity(name)
    if e.type != "mining-drill" or e.mining_speed is None:
        raise ValueError(f"{name} is not a mining drill")
    return e


def search_range(gd: GameData, name: str, pos: Vec2) -> tuple[int, int, int, int]:
    """Inclusive tile range ``(x0, y0, x1, y1)`` whose centres lie in the search square."""
    r = _drill(gd, name).resource_searching_radius
    if r is None:
        raise ValueError(f"{name} has no resource_searching_radius")
    x0 = math.ceil(pos.x - r - 0.5 - _EPS)
    y0 = math.ceil(pos.y - r - 0.5 - _EPS)
    x1 = math.floor(pos.x + r - 0.5 + _EPS)
    y1 = math.floor(pos.y + r - 0.5 + _EPS)
    return x0, y0, x1, y1


def search_tiles(gd: GameData, name: str, pos: Vec2) -> frozenset[Tile]:
    x0, y0, x1, y1 = search_range(gd, name, pos)
    return frozenset(Tile(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def drop_point(gd: GameData, name: str, pos: Vec2, direction: Direction) -> Vec2:
    v = _drill(gd, name).vector_to_place_result
    if v is None:
        raise ValueError(f"{name} has no vector_to_place_result (fluid output?)")
    return pos + Vec2.of(v).rotate(direction)


def drop_tile(gd: GameData, name: str, pos: Vec2, direction: Direction) -> Tile:
    return drop_point(gd, name, pos, direction).floor()


def drop_lane(gd: GameData, name: str, pos: Vec2, direction: Direction, flow: Direction) -> Lane:
    """Lane of a belt (flowing ``flow``) under the drop point that receives the output:
    the half of the belt tile the point lies in."""
    p = drop_point(gd, name, pos, direction)
    rel = p - p.floor().center()
    left = flow.rotate_ccw().vector()
    side = rel.x * left.x + rel.y * left.y
    if abs(side) < _EPS:
        raise ValueError("drop point lies on the belt's centre line")
    return "left" if side > 0 else "right"


def can_mine(gd: GameData, drill: str, resource: str) -> bool:
    """Category matches and no mining fluid is needed (fluid input is out of scope)."""
    e, r = _drill(gd, drill), gd.resource(resource)
    return r.category in e.resource_categories and r.required_fluid is None


def drill_rate(gd: GameData, drill: str, resource: str) -> float:
    """Products per second of one drill on ``resource`` (mining productivity 0)."""
    e, r = _drill(gd, drill), gd.resource(resource)
    return e.mining_speed / r.mining_time * r.results[0][1]
