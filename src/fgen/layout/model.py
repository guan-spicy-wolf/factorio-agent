"""Layout model: placements, interface contracts and units (plan §4.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from fgen.data.schema import GameData
from fgen.geom import footprint
from fgen.geom.vec import Direction, Tile, Vec2

type InterfaceKind = Literal["item", "fluid", "power"]
type LaneSpec = Literal["left", "right", "both"] | None
type Role = Literal["input", "output"]


@dataclass(frozen=True)
class Placement:
    """One entity. ``extra`` carries blueprint fields through untouched
    (``recipe``, ``items``, ``mirror``, ``type`` for undergrounds, ``filters`` ...)."""

    name: str
    position: Vec2
    direction: Direction = Direction.N
    extra: dict[str, Any] = field(default_factory=dict)

    def tiles(self, gd: GameData) -> frozenset[Tile]:
        return footprint.tiles_of(gd, self.name, self.position, self.direction)


@dataclass(frozen=True)
class Interface:
    kind: InterfaceKind
    material: str | None
    tile: Tile
    direction: Direction
    lane: LaneSpec
    rate: float | None
    role: Role
    assumptions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Layout:
    placements: list[Placement] = field(default_factory=list)

    def tiles(self, gd: GameData) -> frozenset[Tile]:
        out: set[Tile] = set()
        for p in self.placements:
            out |= p.tiles(gd)
        return frozenset(out)

    def bounds(self, gd: GameData) -> tuple[Tile, Tile]:
        """Inclusive (top-left, bottom-right) tiles; raises on an empty layout."""
        ts = self.tiles(gd)
        if not ts:
            raise ValueError("empty layout has no bounds")
        return (
            Tile(min(t.x for t in ts), min(t.y for t in ts)),
            Tile(max(t.x for t in ts), max(t.y for t in ts)),
        )

    def size(self, gd: GameData) -> tuple[int, int]:
        lo, hi = self.bounds(gd)
        return hi.x - lo.x + 1, hi.y - lo.y + 1

    def normalized(self, gd: GameData) -> list[Any]:
        """Order-free comparison keys; see :mod:`fgen.layout.compare`."""
        from fgen.layout.compare import normalized

        return normalized(self, gd)

    def same_as(self, other: Layout, gd: GameData) -> bool:
        from fgen.layout.compare import same

        return same(self, other, gd)

    def tile_index(self, gd: GameData) -> dict[Tile, list[int]]:
        """Tile -> indices of placements covering it."""
        idx: dict[Tile, list[int]] = {}
        for i, p in enumerate(self.placements):
            for t in p.tiles(gd):
                idx.setdefault(t, []).append(i)
        return idx


@dataclass(frozen=True)
class Unit:
    layout: Layout
    interfaces: list[Interface] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
