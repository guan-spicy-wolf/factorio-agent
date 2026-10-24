"""M5 (phase 2): end to end on a normally generated map (plan-phase2 §3.5).

Also checks ``RconTerrain`` water/buildable on a map with real lakes (M3).
"""

from __future__ import annotations

import pytest

from fgen.gen.mining_sweep import TOLERANCE
from fgen.geom.vec import Tile
from fgen.sim import lua, mining
from fgen.sim.terrain import RLE_FN, RconTerrain, _rle_rows
from fgen.terrain.model import area_tiles

pytestmark = pytest.mark.headless


def test_water_matches_tile_names(ore_server):
    rt = RconTerrain(ore_server.rcon)
    area = (Tile(-150, -150), Tile(149, 149))
    snap = rt.snapshot(area)
    names = ore_server.rcon.lua_json(
        lua.PRELUDE
        + RLE_FN
        + "local ps = {}\n"
        + "for _, t in pairs(S.find_tiles_filtered{area={{-150, -150}, {150, 150}}, "
        + "name={'water', 'deepwater', 'water-green', 'deepwater-green', 'water-shallow', "
        + "'water-mud'}}) do table.insert(ps, t.position) end\n"
        + lua.emit("{rows=rle(ps)}")
    )
    water = _rle_rows(names["rows"])
    assert water, "the seed should have some lakes in view"
    # shallow water and mud are walkable/buildable: only the deep kinds count as water
    deep = {t for t in water if t in snap.water}
    assert snap.water == frozenset(deep)
    assert not snap.buildable & snap.water
    assert snap.buildable | snap.water == area_tiles(area)  # nothing else blocks here
    assert snap.resources and snap.occupied  # ore, trees and rocks


def test_nearest_iron_patch_end_to_end(ore_server, gd):
    row = mining.real_map_row(ore_server, gd)
    c, m = row["contract"], row["measured"]
    assert row["contract"]["anchor"]["patch_tiles"] >= 50
    assert c["drills"] > 10 and m["machines"] == c["drills"]
    assert m["power"]["inserter_networks"] == 1 and m["power"]["unpowered_inserters"] == 0
    assert "no_minable_resources" not in m["machine_status_probes"]
    assert m["max_abs_deviation"] <= TOLERANCE, (c["interfaces"], m["sink_samples"])
    assert abs(sum(m["sink_throughput"]) - c["total"]) <= TOLERANCE * c["total"]
