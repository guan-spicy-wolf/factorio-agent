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
from fgen.layout.model import (
    Interface,
    LaneMaterial,
    LaneSpec,
    Layout,
    PortGroup,
    Pose,
    Predicate,
    RateProfile,
    Unit,
)

type _Fn[T] = Callable[[T], T]

# Unit.meta entries that are lists of tiles and must move with the layout.
GEOMETRIC_META_KEYS = ("belt_terminals",)


def _swap_lane(lane: LaneSpec) -> LaneSpec:
    return {"left": "right", "right": "left"}.get(lane, lane)  # type: ignore[return-value]


def _swap_requires(reqs: tuple[Predicate, ...]) -> tuple[Predicate, ...]:
    return tuple(
        replace(p, lane=_swap_lane(p.lane)) if isinstance(p, LaneMaterial) else p  # type: ignore[arg-type]
        for p in reqs
    )


def _swap_profile(rp: RateProfile | None) -> RateProfile | None:
    if rp is None or not rp.lanes:
        return rp
    lanes = tuple(sorted((_swap_lane(ln), v) for ln, v in rp.lanes))  # type: ignore[misc]
    return replace(rp, lanes=lanes)  # type: ignore[arg-type]


def _map_iface(i: Interface, tile: _Fn[Tile], d: _Fn[Direction], swap: bool) -> Interface:
    return replace(
        i,
        tile=tile(i.tile),
        direction=d(i.direction),
        lane=_swap_lane(i.lane) if swap else i.lane,
        requires=_swap_requires(i.requires) if swap else i.requires,
        rate_profile=_swap_profile(i.rate_profile) if swap else i.rate_profile,
    )


def _map_group(g: PortGroup, swap: bool) -> PortGroup:
    return replace(g, requires=_swap_requires(g.requires)) if swap else g


def _map_layout(layout: Layout, pos: _Fn[Vec2], d: _Fn[Direction]) -> Layout:
    return Layout(
        [replace(p, position=pos(p.position), direction=d(p.direction)) for p in layout.placements]
    )


def _map_unit(unit: Unit, pos: _Fn[Vec2], tile: _Fn[Tile], d: _Fn[Direction], swap: bool) -> Unit:
    ifaces = [_map_iface(i, tile, d, swap) for i in unit.interfaces]
    meta = dict(unit.meta)
    for key in GEOMETRIC_META_KEYS:
        if key in meta:
            meta[key] = [tile(t) for t in meta[key]]
    groups = tuple(_map_group(g, swap) for g in unit.groups)
    return Unit(_map_layout(unit.layout, pos, d), ifaces, meta, groups)


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


def place_unit(unit: Unit, origin: Tile, pose: Pose) -> Unit:
    """Apply ``pose`` (mirror_x, then rotation) about the unit origin, then translate."""
    u = mirror_x(unit) if pose.mirror else unit
    if pose.rotation != Direction.N:
        u = rotate(u, pose.rotation)
    return translate(u, origin.x, origin.y)


def place_interface(i: Interface, origin: Tile, pose: Pose) -> Interface:
    """``place_unit`` for a single interface."""
    out = i
    if pose.mirror:
        out = _map_iface(out, lambda t: t.mirror_x(), lambda x: x.mirror_x(), True)
    if pose.rotation != Direction.N:
        q = pose.rotation.quarter_turns
        rot = pose.rotation
        out = _map_iface(out, lambda t: t.rotate(rot), lambda x: x.rotate_cw(q), False)
    return replace(out, tile=out.tile.translate(origin.x, origin.y))
