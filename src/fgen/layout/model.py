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


type Supply = Literal["fixed", "on_demand"]
type Approach = Literal["direct", "side", "both"]

# Layout conventions for item ports (not game data):
SIDE_LOAD_STUB = 2  # straight belt tiles a side-load needs in front of an input port: the
#                     joined tile and one behind it, else the join curves (VERIFY §4)
PORT_LEAD = 1  # one more free tile in front of any item port for the link to line up


@dataclass(frozen=True)
class RateProfile:
    """How an interface's rate behaves (plan-phase3a §6 items 2–3, phase 6a).

    ``supply="fixed"``: ``initial`` items/s from the start (equals ``Interface.rate``);
    ``depletion_s_lower_bound`` (anchor units) is a time before which the rate does not
    drop (no drill runs dry earlier); None for free units.
    ``supply="on_demand"``: delivers (or takes) whatever the other side asks, up to
    ``limit`` items/s (None: no limit of its own); ``initial`` is None.
    ``lanes``: per belt lane, the fixed rate (they sum to ``initial``) or the on-demand
    limit; empty when the port is not a belt."""

    initial: float | None = None
    depletion_s_lower_bound: float | None = None
    supply: Supply = "fixed"
    limit: float | None = None
    lanes: tuple[tuple[Lane, float], ...] = ()

    @classmethod
    def fixed(
        cls,
        rate: float,
        lanes: dict[Lane, float] | None = None,
        depletion_s_lower_bound: float | None = None,
    ) -> RateProfile:
        return cls(rate, depletion_s_lower_bound, "fixed", None, _lanes(lanes))

    @classmethod
    def on_demand(
        cls, limit: float | None = None, lanes: dict[Lane, float] | None = None
    ) -> RateProfile:
        return cls(None, None, "on_demand", limit, _lanes(lanes))

    def lane(self, lane: Lane) -> float | None:
        """Fixed rate / on-demand limit on ``lane``; None if not given."""
        return dict(self.lanes).get(lane)


def _lanes(lanes: dict[Lane, float] | None) -> tuple[tuple[Lane, float], ...]:
    return tuple(sorted((lanes or {}).items()))


@dataclass(frozen=True)
class PortSpace:
    """What a link needs in front of an item port (plan-phase3a §6 item 5, phase 6a).

    ``approach``: how a link may join an input — ``direct`` (a belt runs straight into
    the port, both lanes kept), ``side`` (side-load onto one lane over a stub the router
    builds) or ``both``; outputs are ``direct`` (the link continues the belt).
    ``upstream``: straight tiles in front of the port (upstream of an input, downstream
    of an output) kept free for the link: other units stay off them, other links do not
    use them."""

    approach: Approach
    upstream: int

    @classmethod
    def item(cls, approach: Approach) -> PortSpace:
        """The space an item port with ``approach`` needs under the layout conventions:
        a side-load needs its stub plus the lead tile, a direct link the lead tile and
        the link's first belt."""
        if approach == "direct":
            return cls(approach, 1 + PORT_LEAD)
        return cls(approach, SIDE_LOAD_STUB + PORT_LEAD)


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
    assumptions: list[str] = field(default_factory=list)  # prose only; no algorithm reads it
    rate_profile: RateProfile | None = None
    requires: tuple[Predicate, ...] = ()
    entity: str | None = None  # the entity at the port: belt (tier), pole (wire), fluid box
    port_group: str | None = None  # ports sharing one belt (``Unit.groups``)
    space: PortSpace | None = None  # item ports: approach and free tiles in front
    # item ports (plan-phase3a §6 item 8, phase 8): items the unit's own belt run at this
    # port holds when full, on the lanes the port uses (``geom.belt.run_items``). Input:
    # the run from the port tile along the belt to its end; output: the belt tiles that
    # flow into the port, from the first tile an inserter or drill drops onto
    buffer_items: float | None = None
    # fluid ports (plan-phase5a): the fluid's temperature at the port (output: what the
    # unit delivers; input: what it expects, the fluid's default when any will do) and
    # the longest side, in tiles, of the unit's own part of the port's fluid segment (a
    # link may add at most ``pipeline_extent`` minus both ends' extents)
    temperature: float | None = None
    segment_extent: int | None = None

    def on_demand(self) -> bool:
        return self.rate_profile is not None and self.rate_profile.supply == "on_demand"

    def fixed_rate(self) -> float | None:
        """Items/s of a fixed-supply port (None for on-demand or rate-less ports)."""
        if self.rate_profile is not None:
            return self.rate_profile.initial if self.rate_profile.supply == "fixed" else None
        return self.rate

    def lane_rate(self, lane: Lane) -> float | None:
        """Fixed rate (or on-demand limit) carried on ``lane``: the per-lane profile, else
        the whole rate when the port uses only that lane, else None."""
        rp = self.rate_profile
        if rp is not None and rp.lanes:
            return rp.lane(lane) or 0.0
        whole = rp.limit if rp is not None and rp.supply == "on_demand" else self.fixed_rate()
        if self.lane == lane:
            return whole
        if self.lane in ("left", "right"):
            return 0.0
        return None

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
class PortGroup:
    """Ports on one belt (e.g. the ore and fuel inputs of a smelting row's input belt):
    lane requirements of the shared belt hang here (plan-phase3a §6 item 4)."""

    name: str
    requires: tuple[Predicate, ...] = ()

    def lane_materials(self) -> dict[str, str]:
        return {p.lane: p.material for p in self.requires if isinstance(p, LaneMaterial)}


@dataclass(frozen=True)
class Unit:
    layout: Layout
    interfaces: list[Interface] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    groups: tuple[PortGroup, ...] = ()

    def group(self, name: str | None) -> PortGroup | None:
        return next((g for g in self.groups if g.name == name), None) if name else None


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
