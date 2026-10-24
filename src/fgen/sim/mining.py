"""Headless measurement of mining-patch units (plan-phase2 §3.4, §3.5).

Painted cases: ore is painted on the flat field where the hand-written terrain says,
the unit is measured with :class:`fgen.sim.measure.RconCharacterizer` (origin (0, 0):
anchor units carry world coordinates) and the ore is removed again.

Real map: a server on ``config/ore-map-gen-settings.json``; the area around the spawn
is generated and read back, trees and rocks over the nearest iron patch are removed (as
a player would), and the generator runs on the snapshot.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from fgen.data.schema import GameData
from fgen.gen.mining_patch import MiningPatchParams, generate, render
from fgen.gen.mining_sweep import REAL_MAP, Case, compare, contract
from fgen.gen.sweep import blueprint_sha
from fgen.geom import mining as mining_geom
from fgen.geom.vec import Tile
from fgen.layout.model import Unit
from fgen.sim import lua
from fgen.sim.measure import RconCharacterizer
from fgen.sim.server import HeadlessServer
from fgen.sim.terrain import RconTerrain
from fgen.terrain.model import Area, Terrain, nearest_patch

ORIGIN = Tile(0, 0)
ORE_MAP = "ore-map-gen-settings.json"
CLEARABLE_TYPES = ("tree", "simple-entity")  # trees and rocks; cliffs stay obstacles
MEASURE = {"warmup_s": 10.0, "sample_s": 20.0, "samples": 6, "speed": 64.0}


def _compact(m: dict[str, Any]) -> dict[str, Any]:
    """Keep the drill diagnostics that matter; drop per-furnace arrays."""
    detail = m.pop("machine_detail", [])
    for k in ("machine_input_starved", "machine_fuel_starved", "machine_source_empty"):
        m.pop(k, None)
    m["drills_not_always_working"] = [d for d in detail if set(d["status"]) != {"working"}]
    m["drills_without_target"] = [d["position"] for d in detail if d["mining_target"] is None]
    return m


def measure_unit(ch: RconCharacterizer, unit: Unit, **kw: Any) -> dict[str, Any]:
    opts = {**MEASURE, **kw}
    try:
        m = ch.measure(unit, **opts)
    finally:
        ch.clear(unit)
    return _compact(m)


def painted_measurer(srv: HeadlessServer, gd: GameData):  # -> Measurer
    ch = RconCharacterizer(srv, gd, origin=ORIGIN)
    rt = RconTerrain(srv.rcon)

    def measure(case: Case, unit: Unit, terrain: Terrain) -> dict[str, Any]:
        area = terrain.bounds()
        rt.generate(area)
        rt.clear_resources(area)
        res = case.shape.resources()
        rt.paint(res)
        if rt.resources(area) != res:
            raise RuntimeError("painted ore does not read back as painted")
        try:
            return measure_unit(ch, unit)
        finally:
            rt.clear_resources(area)

    return measure


def clear_obstacles(
    srv: HeadlessServer, area: Area, types: tuple[str, ...] = CLEARABLE_TYPES
) -> int:
    (a, b) = area
    box = lua.area_lit(((a.x, a.y), (b.x + 1, b.y + 1)))
    out = srv.rcon.lua_json(
        lua.PRELUDE
        + "local n = 0\n"
        + f"for _, e in pairs(S.find_entities_filtered{{area={box}, type={lua.lit(list(types))}}})"
        + " do e.destroy(); n = n + 1 end\n"
        + lua.emit("{destroyed=n}")
    )
    return int(out["destroyed"])


def real_map_row(
    srv: HeadlessServer,
    gd: GameData,
    params: MiningPatchParams | None = None,
    *,
    radius: int = 150,
    min_tiles: int = 50,
    seed: int | None = None,
) -> dict[str, Any]:
    """Find the nearest iron patch around the spawn, generate, deploy, measure."""
    p = params or MiningPatchParams()
    rt = RconTerrain(srv.rcon, (p.drill, p.belt, p.pole))
    area: Area = (Tile(-radius, -radius), Tile(radius - 1, radius - 1))
    snap = rt.snapshot(area)
    patch = nearest_patch(snap, p.resource, (0.0, 0.0), min_tiles=min_tiles)
    if patch is None:
        raise RuntimeError(f"no {p.resource} patch of {min_tiles}+ tiles within {radius} tiles")
    lo, hi = patch.bounds
    work: Area = (lo.translate(-12, -12), hi.translate(12, 12))
    cleared = clear_obstacles(srv, work)
    snap = rt.snapshot(area, generate=False)
    patch = nearest_patch(snap, p.resource, (0.0, 0.0), min_tiles=min_tiles)
    assert patch is not None
    # the contract assumes ore under every drill: skip drills that could run dry within
    # the measurement (edge tiles of real patches hold only a few ore)
    window_s = MEASURE["warmup_s"] + MEASURE["samples"] * MEASURE["sample_s"]
    unit = generate(p, gd, patch, snap)
    need = math.ceil(mining_geom.drill_rate(gd, p.drill, p.resource) * window_s)
    while (unit.meta["anchor"]["first_dry_s_lower_bound"] or 0) < window_s:
        p = replace(p, min_ore_amount_per_drill=max(need, p.min_ore_amount_per_drill * 2))
        unit = generate(p, gd, patch, snap)
    ch = RconCharacterizer(srv, gd, origin=ORIGIN)
    m = measure_unit(ch, unit)
    c = contract(unit)
    m.update(compare(c, m))
    water = len(snap.water)
    return {
        "case": REAL_MAP,
        "shape": patch.describe(),
        "note": "nearest iron-ore patch to the spawn on a normal map",
        "params": {k: v for k, v in unit.meta["params"].items()},
        "blueprint_sha": blueprint_sha(unit, gd),
        "contract": c,
        "measured": m,
        "real_map": {
            "seed": seed,
            "snapshot_area": [[area[0].x, area[0].y], [area[1].x, area[1].y]],
            "snapshot": {
                "resource_tiles": len(snap.resources),
                "water_tiles": water,
                "occupied_tiles": len(snap.occupied),
                "unbuildable_tiles": (area[1].x - area[0].x + 1) * (area[1].y - area[0].y + 1)
                - len(snap.buildable),
            },
            "cleared_entities": cleared,
            "render": render(unit, gd, snap),
        },
    }
