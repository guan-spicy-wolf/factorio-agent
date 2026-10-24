"""Minimal placement rule (plan-phase3a §3.5): not a siting agent.

``find_spot`` scans square rings around an anchor tile (nearest ring first, a fixed
order inside each ring) for the first origin where the unit, posed by one of the
candidate poses, fits on free buildable tiles with a margin around it and with the
tiles in front of its ports clear. ``place_port`` instead computes the origin and pose
that put one chosen interface on a given tile facing a given direction (used for
fixtures that sit right at the port they serve).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Interface, PlacedUnit, Pose, Unit
from fgen.layout.transform import place_unit

PORT_CLEARANCE = 3  # tiles in front of item ports (stub + approach) kept free


def port_tiles(i: Interface, clearance: int = PORT_CLEARANCE) -> list[Tile]:
    if i.kind == "item":
        d = i.direction if i.role == "output" else i.direction.opposite
        return [i.tile.neighbor(d, k) for k in range(1, clearance + 1)]
    if i.kind == "fluid":
        return [i.tile.neighbor(i.direction)]
    return []


def footprint_ok(
    unit: Unit,
    gd: GameData,
    free: Callable[[Tile], bool],
    margin: int = 1,
    clearance: int = PORT_CLEARANCE,
) -> bool:
    tiles = unit.layout.tiles(gd)
    need: set[Tile] = set(tiles)
    for t in tiles:
        for dx in range(-margin, margin + 1):
            for dy in range(-margin, margin + 1):
                need.add(Tile(t.x + dx, t.y + dy))
    for i in unit.interfaces:
        need.update(port_tiles(i, clearance))
    return all(free(t) for t in need)


def rings(anchor: Tile, max_radius: int) -> Iterable[Tile]:
    yield anchor
    for r in range(1, max_radius + 1):
        for dx in range(-r, r + 1):
            yield Tile(anchor.x + dx, anchor.y - r)
        for dy in range(-r + 1, r + 1):
            yield Tile(anchor.x + r, anchor.y + dy)
        for dx in range(r - 1, -r - 1, -1):
            yield Tile(anchor.x + dx, anchor.y + r)
        for dy in range(r - 1, -r, -1):
            yield Tile(anchor.x - r, anchor.y + dy)


def find_spot(
    name: str,
    unit: Unit,
    gd: GameData,
    free: Callable[[Tile], bool],
    anchor: Tile,
    *,
    poses: Iterable[Pose] = (Pose(),),
    margin: int = 1,
    clearance: int = PORT_CLEARANCE,
    max_radius: int = 80,
) -> PlacedUnit:
    """First fitting placement of ``unit`` whose origin is nearest ``anchor``.
    ``free(tile)`` says whether a tile may be used (buildable and unoccupied)."""
    poses = list(poses)
    for origin in rings(anchor, max_radius):
        for pose in poses:
            w = place_unit(unit, origin, pose)
            if footprint_ok(w, gd, free, margin, clearance):
                return PlacedUnit(name, unit, origin, pose)
    raise ValueError(f"no room for {name} within {max_radius} tiles of {anchor}")


def pose_for(local: Direction, world: Direction) -> Pose:
    """The rotation that turns a ``local`` direction into ``world``."""
    q = (world.quarter_turns - local.quarter_turns) % 4
    return Pose(Direction.N.rotate_cw(q))


def place_port(name: str, unit: Unit, iface: int, tile: Tile, direction: Direction) -> PlacedUnit:
    """Place ``unit`` so its interface ``iface`` lands on ``tile`` facing ``direction``."""
    i = unit.interfaces[iface]
    pose = pose_for(i.direction, direction)
    w = i.world(Tile(0, 0), pose)
    return PlacedUnit(name, unit, Tile(tile.x - w.tile.x, tile.y - w.tile.y), pose)


__all__ = ["find_spot", "place_port", "pose_for", "rings"]
