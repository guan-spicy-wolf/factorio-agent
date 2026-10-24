"""A site: terrain snapshot + placed units + the links to route between them
(plan-phase3a §3).

Units keep their own coordinates; a :class:`fgen.layout.model.PlacedUnit` says where
and how each one sits on the map, and everything here works in world coordinates.
Links name interfaces by ``(unit name, interface index)``; the router
(:mod:`fgen.route.connect`) only reads the interfaces' contracts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from fgen.data.schema import GameData
from fgen.geom.vec import Tile
from fgen.layout.model import Interface, Layout, PlacedUnit, Unit
from fgen.terrain.model import Terrain


class SiteError(ValueError):
    pass


@dataclass(frozen=True)
class Endpoint:
    unit: str
    iface: int

    def __str__(self) -> str:
        return f"{self.unit}#{self.iface}"


@dataclass(frozen=True)
class Link:
    """Connect ``src`` (an output interface) to ``dst`` (an input interface of the same
    kind). Power links are undirected; the router joins all power interfaces anyway."""

    src: Endpoint
    dst: Endpoint
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or f"{self.src}->{self.dst}"


def link(src: tuple[str, int], dst: tuple[str, int], name: str = "") -> Link:
    return Link(Endpoint(*src), Endpoint(*dst), name)


@dataclass(eq=False)
class Site:
    terrain: Terrain
    units: list[PlacedUnit] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)

    def __post_init__(self) -> None:
        names = [u.name for u in self.units]
        if len(set(names)) != len(names):
            raise SiteError(f"unit names must be unique: {names}")

    # -- lookups ----------------------------------------------------------------------
    @cached_property
    def _world(self) -> dict[str, Unit]:
        return {u.name: u.world() for u in self.units}

    def placed(self, name: str) -> PlacedUnit:
        for u in self.units:
            if u.name == name:
                return u
        raise SiteError(f"no unit named {name!r}")

    def world(self, name: str) -> Unit:
        try:
            return self._world[name]
        except KeyError:
            raise SiteError(f"no unit named {name!r}") from None

    def interface(self, ep: Endpoint) -> Interface:
        ifaces = self.world(ep.unit).interfaces
        if not 0 <= ep.iface < len(ifaces):
            raise SiteError(f"{ep}: unit has {len(ifaces)} interfaces")
        return ifaces[ep.iface]

    def interfaces(self) -> Iterable[tuple[Endpoint, Interface]]:
        for u in self.units:
            for k, i in enumerate(self.world(u.name).interfaces):
                yield Endpoint(u.name, k), i

    # -- geometry ---------------------------------------------------------------------
    def occupancy(self, gd: GameData) -> dict[Tile, str]:
        """Tile -> unit name for every tile any unit covers; raises on overlap."""
        out: dict[Tile, str] = {}
        for u in self.units:
            for t in self.world(u.name).layout.tiles(gd):
                other = out.get(t)
                if other is not None and other != u.name:
                    raise SiteError(f"units {other!r} and {u.name!r} overlap at {t}")
                out[t] = u.name
        return out

    def check(self, gd: GameData) -> list[str]:
        """Problems with the placement itself: overlaps and unbuildable tiles."""
        problems: list[str] = []
        try:
            self.occupancy(gd)
        except SiteError as exc:
            problems.append(str(exc))
        for u in self.units:
            bad = [t for t in self.world(u.name).layout.tiles(gd) if not self.terrain.can_build(t)]
            if bad and not self.world(u.name).meta.get("anchor"):
                problems.append(f"{u.name}: {len(bad)} tiles not buildable, e.g. {bad[0]}")
        for ln in self.links:
            a, b = self.interface(ln.src), self.interface(ln.dst)
            if a.kind != b.kind:
                problems.append(f"{ln.label}: {a.kind} to {b.kind}")
            elif a.kind != "power" and (a.role, b.role) != ("output", "input"):
                problems.append(f"{ln.label}: {a.role} -> {b.role}, want output -> input")
        return problems

    def merged(self, extra: Iterable[Layout] = ()) -> Layout:
        """All units' world placements (in unit order), then the ``extra`` layouts."""
        ps = [p for u in self.units for p in self.world(u.name).layout.placements]
        for lay in extra:
            ps += lay.placements
        return Layout(ps)

    def belt_terminals(self) -> list[Tile]:
        return [t for u in self.units for t in self.world(u.name).meta.get("belt_terminals", [])]

    def fluids(self) -> dict[int, str]:
        """Merged placement index -> fluid, from each unit's ``meta['fluids']``."""
        out: dict[int, str] = {}
        base = 0
        for u in self.units:
            w = self.world(u.name)
            for k, f in (w.meta.get("fluids") or {}).items():
                out[base + int(k)] = f
            base += len(w.layout.placements)
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "units": [
                {
                    "name": u.name,
                    "origin": [u.origin.x, u.origin.y],
                    "rotation": u.pose.rotation.name,
                    "mirror": u.pose.mirror,
                    "family": u.unit.meta.get("family")
                    or u.unit.meta.get("fixture", {}).get("kind"),
                }
                for u in self.units
            ],
            "links": [ln.label for ln in self.links],
        }
