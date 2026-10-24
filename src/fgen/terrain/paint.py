"""Ore shapes for tests and fixtures (plan-phase2 §3.1).

Each function returns ``{Tile: amount}``; ``to_resources`` tags them with a resource
name for :class:`fgen.terrain.model.Terrain`. Laying them on a map is
``fgen.sim.terrain.RconTerrain.paint``. A :class:`ShapeSpec` names a shape and its
arguments so it can be rebuilt and described in characterization files.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fgen.geom.vec import Tile

type Shape = dict[Tile, int]


def rectangle(x: int, y: int, w: int, h: int, amount: int) -> Shape:
    if w <= 0 or h <= 0:
        raise ValueError("rectangle needs a positive size")
    return {Tile(x + i, y + j): amount for i in range(w) for j in range(h)}


def l_shape(x: int, y: int, w: int, h: int, arm: int, amount: int) -> Shape:
    """An L: the full-height left column block (``arm`` wide) plus the full-width
    bottom block (``arm`` tall), inside a ``w`` x ``h`` box at (x, y)."""
    if not 0 < arm < min(w, h):
        raise ValueError("arm must be smaller than both sides")
    out = rectangle(x, y, arm, h, amount)
    out.update(rectangle(x, y + h - arm, w, arm, amount))
    return out


def minus(shape: Mapping[Tile, int], tiles: Iterable[Tile]) -> Shape:
    drop = set(tiles)
    return {t: a for t, a in shape.items() if t not in drop}


def with_hole(shape: Mapping[Tile, int], x: int, y: int, w: int, h: int) -> Shape:
    return minus(shape, rectangle(x, y, w, h, 0))


def blob(
    seed: int, n_tiles: int, amount: int, *, cx: int = 0, cy: int = 0, spread: int = 0
) -> Shape:
    """A random 4-connected blob of ``n_tiles`` grown from tile (``cx``, ``cy``). ``spread`` > 0
    varies each tile's amount by up to that many units (deterministic for ``seed``)."""
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    rng = random.Random(seed)
    center = Tile(cx, cy)
    tiles = {center}
    frontier = [center]
    while len(tiles) < n_tiles:
        t = rng.choice(frontier)
        nb = [
            Tile(t.x + dx, t.y + dy)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            if Tile(t.x + dx, t.y + dy) not in tiles
        ]
        if not nb:
            frontier.remove(t)
            continue
        n = rng.choice(nb)
        tiles.add(n)
        frontier.append(n)
    return {t: amount + (rng.randint(-spread, spread) if spread else 0) for t in sorted(tiles)}


def to_resources(shape: Mapping[Tile, int], resource: str) -> dict[Tile, tuple[str, int]]:
    return {t: (resource, a) for t, a in shape.items()}


SHAPES = {"rectangle": rectangle, "l_shape": l_shape, "blob": blob}


@dataclass(frozen=True)
class ShapeSpec:
    """A named shape (``kind`` in :data:`SHAPES`), its arguments and optional holes
    ``(x, y, w, h)``; ``build()`` returns the tiles."""

    kind: str
    args: Mapping[str, Any]
    holes: tuple[tuple[int, int, int, int], ...] = field(default=())
    resource: str = "iron-ore"

    def build(self) -> Shape:
        shape = SHAPES[self.kind](**self.args)
        for x, y, w, h in self.holes:
            shape = with_hole(shape, x, y, w, h)
        return shape

    def resources(self) -> dict[Tile, tuple[str, int]]:
        return to_resources(self.build(), self.resource)

    def describe(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.args.items())
        holes = "".join(f" minus {w}x{h} hole at ({x},{y})" for x, y, w, h in self.holes)
        return f"{self.resource} {self.kind}({args}){holes}"
