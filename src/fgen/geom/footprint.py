"""Entity footprints: which tiles an entity occupies."""

from __future__ import annotations

import math

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile, Vec2

_EPS = 1e-6


def size(gd: GameData, name: str, direction: Direction = Direction.N) -> tuple[int, int]:
    """(width, height) in tiles; swapped for E/W-facing entities."""
    e = gd.entity(name)
    (x1, y1), (x2, y2) = e.collision_box
    w = e.tile_width if e.tile_width is not None else math.ceil(x2 - x1 - _EPS)
    h = e.tile_height if e.tile_height is not None else math.ceil(y2 - y1 - _EPS)
    d = Direction(direction)
    if not d.is_cardinal:
        raise ValueError(f"footprint for non-cardinal direction {d.name} is not supported")
    if d in (Direction.E, Direction.W):
        w, h = h, w
    return w, h


def top_left_tile(gd: GameData, name: str, pos: Vec2, direction: Direction) -> Tile:
    """Top-left tile of the footprint. Raises if ``pos`` is not a legal centre."""
    w, h = size(gd, name, direction)
    left, top = pos.x - w / 2, pos.y - h / 2
    tx, ty = round(left), round(top)
    if abs(left - tx) > _EPS or abs(top - ty) > _EPS:
        raise ValueError(
            f"{name} ({w}x{h}) at ({pos.x}, {pos.y}) is not tile-aligned: "
            "even sizes need integer centres, odd sizes need .5 centres"
        )
    return Tile(tx, ty)


def is_aligned(gd: GameData, name: str, pos: Vec2, direction: Direction) -> bool:
    try:
        top_left_tile(gd, name, pos, direction)
    except ValueError:
        return False
    return True


def tiles_of(gd: GameData, name: str, pos: Vec2, direction: Direction) -> frozenset[Tile]:
    w, h = size(gd, name, direction)
    t = top_left_tile(gd, name, pos, direction)
    return frozenset(Tile(t.x + i, t.y + j) for i in range(w) for j in range(h))


def aabb_of(gd: GameData, name: str, pos: Vec2, direction: Direction) -> tuple[Vec2, Vec2]:
    """World-space collision box (left-top, right-bottom) after rotation."""
    (x1, y1), (x2, y2) = gd.entity(name).collision_box
    a = Vec2(x1, y1).rotate(direction)
    b = Vec2(x2, y2).rotate(direction)
    lt = Vec2(min(a.x, b.x), min(a.y, b.y))
    rb = Vec2(max(a.x, b.x), max(a.y, b.y))
    return lt + pos, rb + pos


def center_for_tile_anchor(
    gd: GameData, name: str, top_left: Tile, direction: Direction = Direction.N
) -> Vec2:
    """Centre position that puts the footprint's top-left tile at ``top_left``."""
    w, h = size(gd, name, direction)
    return Vec2(top_left.x + w / 2, top_left.y + h / 2)
