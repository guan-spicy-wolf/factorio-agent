"""Fluid connection points (2.0 ``pipe_connections`` format). Details: phase 5."""

from __future__ import annotations

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile, Vec2


def connection_points(
    gd: GameData,
    name: str,
    pos: Vec2,
    direction: Direction,
    *,
    include_underground: bool = False,
) -> list[tuple[Tile, Direction]]:
    """Tiles of the entity that carry a pipe connection and the outward direction.

    In 2.0 each connection's ``position`` is inside the entity (N-facing) and its
    ``direction`` points to the neighbour it connects to.
    """
    e = gd.entity(name)
    out: list[tuple[Tile, Direction]] = []
    for fb in (e.fluid_box, e.output_fluid_box):
        if fb is None:
            continue
        for c in fb.pipe_connections:
            if c.connection_type != "normal" and not include_underground:
                continue
            tile = (pos + Vec2.of(c.position).rotate(direction)).floor()
            d = Direction(c.direction).rotate_cw(Direction(direction).quarter_turns)
            out.append((tile, d))
    return out
