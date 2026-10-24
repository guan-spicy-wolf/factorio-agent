"""Terrain snapshots and ore patches (plan-phase2 §3.1).

A :class:`Terrain` is a frozen picture of one map area: which tiles can be built on,
which hold a resource entity (name and amount), which are water and which are already
covered by some entity. Generators read only this snapshot, never the game, so they
can be unit-tested with hand-written terrain (``Terrain.from_resources``). The headless
implementation (``fgen.sim.terrain.RconTerrain``) exports the same snapshot.

Everything here is a pure function of tiles; no game numbers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from fgen.geom.vec import Tile

type Area = tuple[Tile, Tile]  # inclusive (top-left, bottom-right) tiles

_NEIGHBOURS = (Tile(1, 0), Tile(-1, 0), Tile(0, 1), Tile(0, -1))


def area_tiles(area: Area) -> frozenset[Tile]:
    (a, b) = area
    return frozenset(Tile(x, y) for x in range(a.x, b.x + 1) for y in range(a.y, b.y + 1))


def bounds_of(tiles: Iterable[Tile]) -> Area:
    ts = list(tiles)
    if not ts:
        raise ValueError("no tiles")
    return (
        Tile(min(t.x for t in ts), min(t.y for t in ts)),
        Tile(max(t.x for t in ts), max(t.y for t in ts)),
    )


@dataclass(frozen=True, eq=False)
class Terrain:
    """``buildable``: land tiles an ordinary building may stand on (resource tiles
    included; water, out-of-map and other blocking tiles excluded). ``resources``: tile
    -> (resource name, amount). ``water``: water tiles. ``occupied``: tiles already
    covered by a non-resource entity (trees, rocks, cliffs, buildings)."""

    buildable: frozenset[Tile]
    resources: Mapping[Tile, tuple[str, int]] = field(default_factory=dict)
    water: frozenset[Tile] = frozenset()
    occupied: frozenset[Tile] = frozenset()

    @classmethod
    def from_resources(
        cls,
        resources: Mapping[Tile, tuple[str, int]],
        *,
        area: Area | None = None,
        margin: int = 8,
        water: Iterable[Tile] = (),
        occupied: Iterable[Tile] = (),
        blocked: Iterable[Tile] = (),
    ) -> Terrain:
        """Flat land over ``area`` (default: the resources' bounds plus ``margin``)
        with the given resources; ``water`` and ``blocked`` tiles are not buildable."""
        if area is None:
            lo, hi = bounds_of(resources) if resources else (Tile(0, 0), Tile(0, 0))
            area = (lo.translate(-margin, -margin), hi.translate(margin, margin))
        w = frozenset(water)
        land = area_tiles(area) - w - frozenset(blocked)
        return cls(
            buildable=land,
            resources=dict(resources),
            water=w,
            occupied=frozenset(occupied),
        )

    def can_build(self, tile: Tile) -> bool:
        return tile in self.buildable and tile not in self.occupied

    def can_build_all(self, tiles: Iterable[Tile]) -> bool:
        return all(self.can_build(t) for t in tiles)

    def resource_at(self, tile: Tile) -> tuple[str, int] | None:
        return self.resources.get(tile)

    def resource_tiles(self, resource: str) -> dict[Tile, int]:
        return {t: a for t, (n, a) in self.resources.items() if n == resource}

    def bounds(self) -> Area:
        return bounds_of(self.buildable | self.water | frozenset(self.resources))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Terrain):
            return NotImplemented
        return (
            self.buildable == other.buildable
            and dict(self.resources) == dict(other.resources)
            and self.water == other.water
            and self.occupied == other.occupied
        )

    __hash__ = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Patch:
    """One 4-connected group of tiles of a single resource."""

    resource: str
    tiles: frozenset[Tile]
    amounts: Mapping[Tile, int] = field(default_factory=dict, compare=False)

    @property
    def bounds(self) -> Area:
        return bounds_of(self.tiles)

    @property
    def total_amount(self) -> int:
        return sum(self.amounts.get(t, 0) for t in self.tiles)

    @property
    def size(self) -> tuple[int, int]:
        lo, hi = self.bounds
        return hi.x - lo.x + 1, hi.y - lo.y + 1

    def centroid(self) -> tuple[float, float]:
        n = len(self.tiles)
        return (
            sum(t.x + 0.5 for t in self.tiles) / n,
            sum(t.y + 0.5 for t in self.tiles) / n,
        )

    def describe(self) -> str:
        (lo, hi), (w, h) = self.bounds, self.size
        return (
            f"{self.resource}: {len(self.tiles)} tiles in {w}x{h} at ({lo.x},{lo.y})..("
            f"{hi.x},{hi.y}), {self.total_amount} total"
        )


def components(tiles: Iterable[Tile]) -> list[frozenset[Tile]]:
    """4-connected components, largest first (ties: top-left-most first)."""
    left = set(tiles)
    out: list[frozenset[Tile]] = []
    while left:
        start = left.pop()
        comp, stack = {start}, [start]
        while stack:
            t = stack.pop()
            for d in _NEIGHBOURS:
                n = t + d
                if n in left:
                    left.discard(n)
                    comp.add(n)
                    stack.append(n)
        out.append(frozenset(comp))
    out.sort(key=lambda c: (-len(c), min(c)))
    return out


def patches(terrain: Terrain, resource: str) -> list[Patch]:
    """Split the tiles of ``resource`` into 4-connected patches, largest first."""
    amounts = terrain.resource_tiles(resource)
    return [Patch(resource, comp, {t: amounts[t] for t in comp}) for comp in components(amounts)]


def nearest_patch(
    terrain: Terrain, resource: str, point: tuple[float, float], *, min_tiles: int = 1
) -> Patch | None:
    """The patch with at least ``min_tiles`` tiles whose closest tile centre is nearest
    to ``point``."""

    def dist(p: Patch) -> float:
        return min((t.x + 0.5 - point[0]) ** 2 + (t.y + 0.5 - point[1]) ** 2 for t in p.tiles)

    cands = [p for p in patches(terrain, resource) if len(p.tiles) >= min_tiles]
    return min(cands, key=dist) if cands else None
