"""Rigid transforms of layouts and units (plan §4.3).

All transforms act on world coordinates about the origin. ``rotate`` takes the
direction that N is turned into (E = 90 degrees clockwise). Interface tiles,
directions and lanes move with the layout; mirroring swaps ``left``/``right``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout.model import LaneSpec, Layout, Unit

type _Fn[T] = Callable[[T], T]


def _swap_lane(lane: LaneSpec) -> LaneSpec:
    return {"left": "right", "right": "left"}.get(lane, lane)  # type: ignore[return-value]


def _map_layout(layout: Layout, pos: _Fn[Vec2], d: _Fn[Direction]) -> Layout:
    return Layout(
        [replace(p, position=pos(p.position), direction=d(p.direction)) for p in layout.placements]
    )


def _map_unit(unit: Unit, pos: _Fn[Vec2], tile: _Fn[Tile], d: _Fn[Direction], swap: bool) -> Unit:
    ifaces = [
        replace(
            i,
            tile=tile(i.tile),
            direction=d(i.direction),
            lane=_swap_lane(i.lane) if swap else i.lane,
        )
        for i in unit.interfaces
    ]
    return Unit(_map_layout(unit.layout, pos, d), ifaces, dict(unit.meta))


def translate[T: (Layout, Unit)](obj: T, dx: int, dy: int) -> T:
    """Translate by whole tiles (keeps every entity tile-aligned)."""
    off = Vec2(dx, dy)
    pos: _Fn[Vec2] = lambda v: v + off  # noqa: E731
    same: _Fn[Direction] = lambda d: d  # noqa: E731
    if isinstance(obj, Unit):
        return _map_unit(obj, pos, lambda t: t.translate(dx, dy), same, False)
    return _map_layout(obj, pos, same)


def rotate[T: (Layout, Unit)](obj: T, d: Direction) -> T:
    q = d.quarter_turns
    pos: _Fn[Vec2] = lambda v: v.rotate(d)  # noqa: E731
    dirf: _Fn[Direction] = lambda x: x.rotate_cw(q)  # noqa: E731
    if isinstance(obj, Unit):
        return _map_unit(obj, pos, lambda t: t.rotate(d), dirf, False)
    return _map_layout(obj, pos, dirf)


def mirror_x[T: (Layout, Unit)](obj: T) -> T:
    pos: _Fn[Vec2] = lambda v: v.mirror_x()  # noqa: E731
    dirf: _Fn[Direction] = lambda x: x.mirror_x()  # noqa: E731
    if isinstance(obj, Unit):
        return _map_unit(obj, pos, lambda t: t.mirror_x(), dirf, True)
    return _map_layout(obj, pos, dirf)


def mirror_y[T: (Layout, Unit)](obj: T) -> T:
    pos: _Fn[Vec2] = lambda v: v.mirror_y()  # noqa: E731
    dirf: _Fn[Direction] = lambda x: x.mirror_y()  # noqa: E731
    if isinstance(obj, Unit):
        return _map_unit(obj, pos, lambda t: t.mirror_y(), dirf, True)
    return _map_layout(obj, pos, dirf)


def normalize[T: (Layout, Unit)](obj: T, gd: GameData) -> T:
    """Translate so the layout's top-left tile is (0, 0)."""
    layout = obj.layout if isinstance(obj, Unit) else obj
    lo, _ = layout.bounds(gd)
    return translate(obj, -lo.x, -lo.y)
