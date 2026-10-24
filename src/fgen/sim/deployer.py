"""``RconDeployer``: put layouts into a running game and read them back (plan §3.3).

Main path is the blueprint: ``unit_to_blueprint`` -> ``encode`` -> in game
``import_stack`` -> ``build_blueprint{force_build=true}`` -> ``revive`` every ghost, so
the phase-0 exporter is checked by the game itself. ``place_direct`` creates the same
entities one by one with ``surface.create_entity`` as a control.

Coordinates: a layout's positions are unit coordinates; ``origin`` (a tile) is added to
get world positions, and :meth:`read_back` subtracts it again.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from fgen.blueprint import codec
from fgen.blueprint.export import layout_to_blueprint
from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout.model import Layout, Placement, Unit
from fgen.sim import lua
from fgen.sim.rcon import RconClient

type Area = tuple[Tile, Tile]


class DeployError(RuntimeError):
    pass


@dataclass
class PlaceReport:
    origin: Tile
    entities: int
    revived: int
    unit_numbers: list[int] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    import_result: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def _as_list(x: Any) -> list[Any]:
    """``helpers.table_to_json`` writes an empty Lua table as ``{}``."""
    if isinstance(x, dict):
        return [] if not x else [x[k] for k in sorted(x, key=int)]
    return list(x or [])


def world_area(layout: Layout, gd: GameData, origin: Tile, margin: int = 0) -> lua.Area:
    lo, hi = layout.bounds(gd)
    return (
        (origin.x + lo.x - margin, origin.y + lo.y - margin),
        (origin.x + hi.x + 1 + margin, origin.y + hi.y + 1 + margin),
    )


def tile_area_to_lua(area: Area) -> lua.Area:
    """Inclusive tile area -> world rectangle covering those tiles."""
    a, b = area
    return (a.x, a.y), (b.x + 1, b.y + 1)


def entity_to_placement(e: dict[str, Any], origin: Tile) -> Placement:
    extra: dict[str, Any] = {}
    if e.get("belt_to_ground_type"):
        extra["type"] = e["belt_to_ground_type"]
    if e.get("loader_type"):
        extra["type"] = e["loader_type"]
    if e.get("recipe"):
        extra["recipe"] = e["recipe"]
    pos = e["position"]
    return Placement(
        e["name"],
        Vec2(float(pos["x"]) - origin.x, float(pos["y"]) - origin.y),
        Direction.from_blueprint(int(e.get("direction", 0))),
        extra,
    )


class RconDeployer:
    """Implements ``fgen.sim.protocol.Deployer`` over RCON."""

    def __init__(self, rcon: RconClient, gd: GameData) -> None:
        self.rcon = rcon
        self.gd = gd
        self.last_origin = Tile(0, 0)

    # -- placing ----------------------------------------------------------------
    def place(self, unit: Unit, origin: Tile) -> PlaceReport:
        return self.place_layout(unit.layout, origin, icon=unit.meta.get("icon"))

    def place_layout(self, layout: Layout, origin: Tile, icon: str | None = None) -> PlaceReport:
        if not layout.placements:
            raise DeployError("empty layout")
        bp = layout_to_blueprint(layout, self.gd, label="fgen", icon=icon)
        s = codec.encode(bp)
        min_x = min(p.position.x for p in layout.placements) + origin.x
        min_y = min(p.position.y for p in layout.placements) + origin.y
        lo, hi = layout.bounds(self.gd)
        centre = (origin.x + (lo.x + hi.x + 1) / 2, origin.y + (lo.y + hi.y + 1) / 2)
        out = self.rcon.lua_json(lua.build_blueprint(s, centre, (min_x, min_y)))
        self.last_origin = origin
        rep = PlaceReport(
            origin=origin,
            entities=len(layout.placements),
            revived=int(out["revived"]),
            unit_numbers=[int(u) for u in _as_list(out["unit_numbers"])],
            failed=_as_list(out["failed"]),
            import_result=int(out["import"]),
            raw=out,
        )
        if rep.import_result == 1:
            raise DeployError("game rejected the blueprint string (import_stack returned 1)")
        off = [float(v) for v in _as_list(out["offset"])]
        if any(abs(v) > 1e-6 for v in off):
            raise DeployError(f"blueprint landed off by {off} after correction")
        if int(out["ghosts"]) != rep.entities or rep.failed:
            raise DeployError(
                f"built {out['ghosts']} ghosts / revived {rep.revived} of {rep.entities} "
                f"entities; failed: {rep.failed[:5]}"
            )
        return rep

    def place_direct(self, layout: Layout, origin: Tile) -> PlaceReport:
        """Control path: ``create_entity`` per placement (no blueprint involved)."""
        specs = []
        for p in layout.placements:
            spec: dict[str, Any] = {
                "name": p.name,
                "position": [p.position.x + origin.x, p.position.y + origin.y],
                "direction": int(p.direction),
            }
            t = self.gd.entity(p.name).type
            if t in ("underground-belt", "loader", "loader-1x1"):
                spec["type"] = p.extra.get("type", "input")
            if p.extra.get("recipe"):
                spec["recipe"] = p.extra["recipe"]
            specs.append(spec)
        out = self.rcon.lua_json(lua.create_entities(specs))
        self.last_origin = origin
        failed = [specs[int(i) - 1] for i in _as_list(out["failed"])]
        if failed:
            raise DeployError(f"create_entity failed for {failed[:5]}")
        return PlaceReport(origin, len(specs), int(out["created"]), failed=failed, raw=out)

    # -- reading ----------------------------------------------------------------
    def read_entities(self, area: lua.Area) -> list[dict[str, Any]]:
        return _as_list(self.rcon.lua_json(lua.read_entities(area)))

    def read_back(
        self,
        area: Area,
        origin: Tile | None = None,
        exclude: Iterable[int] = (),
    ) -> Layout:
        """Entities in the inclusive tile ``area`` as a Layout in unit coordinates
        (``origin`` defaults to the last placement's). ``exclude`` drops entities by
        ``unit_number`` (e.g. harness parts)."""
        o = self.last_origin if origin is None else origin
        skip = set(exclude)
        ents = [
            e
            for e in self.read_entities(tile_area_to_lua(area))
            if e.get("unit_number") not in skip
        ]
        return Layout([entity_to_placement(e, o) for e in ents])

    def read_back_layout(self, layout: Layout, origin: Tile, **kw: Any) -> Layout:
        """Read back exactly the area ``layout`` occupies when placed at ``origin``."""
        lo, hi = layout.bounds(self.gd)
        area = (Tile(origin.x + lo.x, origin.y + lo.y), Tile(origin.x + hi.x, origin.y + hi.y))
        return self.read_back(area, origin, **kw)

    def clear(self, area: Area) -> int:
        out = self.rcon.lua_json(lua.clear_area(tile_area_to_lua(area)))
        return int(out["destroyed"])
