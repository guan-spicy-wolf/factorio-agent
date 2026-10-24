"""Vectors, tiles and the 16-way ``Direction`` (plan §3).

Conventions: x grows east, y grows south. Tile ``(tx, ty)`` is the 1x1 square whose
top-left corner is at ``(tx, ty)``. Rotating clockwise by 90 degrees (N -> E) maps
``(x, y)`` to ``(-y, x)``; e.g. north ``(0, -1)`` becomes east ``(1, 0)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

_EPS = 1e-9


class Direction(IntEnum):
    """Factorio 2.0 16-way direction. Values equal the blueprint integers."""

    N = 0
    NNE = 1
    NE = 2
    ENE = 3
    E = 4
    ESE = 5
    SE = 6
    SSE = 7
    S = 8
    SSW = 9
    SW = 10
    WSW = 11
    W = 12
    WNW = 13
    NW = 14
    NNW = 15

    @property
    def is_cardinal(self) -> bool:
        return self.value % 4 == 0

    def rotate_cw(self, quarters: int = 1) -> Direction:
        """Rotate clockwise by ``quarters`` x 90 degrees (negative = counter-clockwise)."""
        return Direction((self.value + 4 * quarters) % 16)

    def rotate_ccw(self, quarters: int = 1) -> Direction:
        return self.rotate_cw(-quarters)

    @property
    def opposite(self) -> Direction:
        return Direction((self.value + 8) % 16)

    def mirror_x(self) -> Direction:
        """Direction after negating x: E <-> W, N and S unchanged."""
        return Direction((16 - self.value) % 16)

    def mirror_y(self) -> Direction:
        """Direction after negating y: N <-> S, E and W unchanged."""
        return Direction((8 - self.value) % 16)

    @property
    def quarter_turns(self) -> int:
        """Number of clockwise 90-degree turns from N; only for cardinal directions."""
        if not self.is_cardinal:
            raise ValueError(f"{self.name} is not a cardinal direction")
        return self.value // 4

    def vector(self) -> Vec2:
        """Unit vector of a cardinal direction (N = (0, -1))."""
        return Vec2(0.0, -1.0).rotate(self)

    def tile_offset(self) -> Tile:
        v = self.vector()
        return Tile(round(v.x), round(v.y))

    def to_blueprint(self) -> int:
        """2.0 blueprints store the 16-way value directly."""
        return int(self.value)

    @classmethod
    def from_blueprint(cls, value: int) -> Direction:
        return cls(int(value) % 16)

    @classmethod
    def from_legacy_8way(cls, value: int) -> Direction:
        """Factorio 1.1 blueprints used 8 directions (E = 2)."""
        return cls((int(value) * 2) % 16)


CARDINALS: tuple[Direction, ...] = (Direction.N, Direction.E, Direction.S, Direction.W)


@dataclass(frozen=True, slots=True)
class Vec2:
    x: float
    y: float

    def __add__(self, other: Vec2) -> Vec2:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Vec2:
        return Vec2(self.x - other.x, self.y - other.y)

    def __neg__(self) -> Vec2:
        return Vec2(-self.x, -self.y)

    def __mul__(self, k: float) -> Vec2:
        return Vec2(self.x * k, self.y * k)

    def rotate(self, d: Direction) -> Vec2:
        """Rotate a vector defined for N-facing into direction ``d`` (cardinal only)."""
        x, y = self.x, self.y
        for _ in range(Direction(d).quarter_turns):
            x, y = -y, x
        return Vec2(x + 0.0, y + 0.0)  # normalise -0.0

    def mirror_x(self) -> Vec2:
        return Vec2(-self.x + 0.0, self.y)

    def mirror_y(self) -> Vec2:
        return Vec2(self.x, -self.y + 0.0)

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def distance(self, other: Vec2) -> float:
        return (self - other).length()

    def floor(self) -> Tile:
        """The tile containing this point (tolerant to float noise)."""
        return Tile(math.floor(self.x + _EPS), math.floor(self.y + _EPS))

    @classmethod
    def of(cls, v: tuple[float, float]) -> Vec2:
        return cls(float(v[0]), float(v[1]))


@dataclass(frozen=True, slots=True, order=True)
class Tile:
    x: int
    y: int

    def __add__(self, other: Tile) -> Tile:
        return Tile(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Tile) -> Tile:
        return Tile(self.x - other.x, self.y - other.y)

    def neighbor(self, d: Direction, n: int = 1) -> Tile:
        off = d.tile_offset()
        return Tile(self.x + off.x * n, self.y + off.y * n)

    def center(self) -> Vec2:
        return Vec2(self.x + 0.5, self.y + 0.5)

    # Tile-level versions of the point transforms: the image of the unit square.
    def rotate(self, d: Direction) -> Tile:
        x, y = self.x, self.y
        for _ in range(Direction(d).quarter_turns):
            x, y = -y - 1, x
        return Tile(x, y)

    def mirror_x(self) -> Tile:
        return Tile(-self.x - 1, self.y)

    def mirror_y(self) -> Tile:
        return Tile(self.x, -self.y - 1)

    def translate(self, dx: int, dy: int) -> Tile:
        return Tile(self.x + dx, self.y + dy)
