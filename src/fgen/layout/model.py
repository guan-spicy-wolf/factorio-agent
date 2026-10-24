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
type Lane = Literal["left", "right"]


@dataclass(frozen=True)
class RateProfile:
    """How an interface's rate behaves over time. ``initial`` equals ``Interface.rate``;
    ``depletion_s_lower_bound`` (anchor units) is a time before which the rate does not
    drop (no drill runs dry earlier); None for free units."""

    initial: float
    depletion_s_lower_bound: float | None = None


@dataclass(frozen=True)
class LaneMaterial:
    """Only ``material`` may ride on ``lane`` of this belt interface."""

    lane: Lane
    material: str


@dataclass(frozen=True)
class MinRate:
    """The interface needs at least ``value`` items (or fluid units) per second for the
    unit to reach its target."""

    value: float


@dataclass(frozen=True)
class Compressed:
    """The unit assumes a fully compressed input (recorded only, never checked)."""


type Predicate = LaneMaterial | MinRate | Compressed


@dataclass(frozen=True)
class Pose:
    """Rigid placement of a unit: mirror (x -> -x) first if ``mirror``, then rotate so
    that N becomes ``rotation``; translation is given separately (an origin tile)."""

    rotation: Direction = Direction.N
    mirror: bool = False


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
    rate_profile: RateProfile | None = None
    requires: tuple[Predicate, ...] = ()

    def world(self, origin: Tile, transform: Pose | None = None) -> Interface:
        """This interface after placing its unit with ``transform`` at ``origin``."""
        from fgen.layout.transform import place_interface

        return place_interface(self, origin, transform or Pose())

    def lane_materials(self) -> dict[str, str]:
        """``LaneMaterial`` requirements as lane -> material."""
        return {p.lane: p.material for p in self.requires if isinstance(p, LaneMaterial)}

    def min_rate(self) -> float | None:
        vals = [p.value for p in self.requires if isinstance(p, MinRate)]
        return max(vals) if vals else None


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


@dataclass(frozen=True)
class PlacedUnit:
    """A unit placed on the map: ``pose`` applied about the unit's own origin, then
    translated by ``origin``. ``name`` identifies it inside a site."""

    name: str
    unit: Unit
    origin: Tile = field(default_factory=lambda: Tile(0, 0))
    pose: Pose = field(default_factory=Pose)

    def world(self) -> Unit:
        """The unit in world coordinates (layout, interfaces and geometric meta)."""
        from fgen.layout.transform import place_unit

        return place_unit(self.unit, self.origin, self.pose)

    def interfaces_world(self) -> list[Interface]:
        return [i.world(self.origin, self.pose) for i in self.unit.interfaces]
