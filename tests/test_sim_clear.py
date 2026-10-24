"""M1 (phase 3a): clearing obstacles on a normal map agrees with the snapshot."""

from __future__ import annotations

import pytest

from fgen.geom.vec import Tile
from fgen.sim import clear
from fgen.sim.terrain import RconTerrain
from fgen.terrain.model import nearest_patch

pytestmark = pytest.mark.headless


def test_clear_counts_match_snapshot(ore_server):
    rcon = ore_server.rcon
    rt = RconTerrain(rcon)
    snap = rt.snapshot((Tile(-150, -150), Tile(149, 149)))
    patch = nearest_patch(snap, "iron-ore", (0.0, 0.0), min_tiles=50)
    assert patch is not None
    lo, hi = patch.bounds
    area = (lo.translate(-30, -30), hi.translate(30, 30))
    # something to clear besides trees and rocks: an item on the ground and a corpse
    rcon.lua(
        "local S = game.surfaces[1]\n"
        f"S.spill_item_stack{{position={{{lo.x - 5}, {lo.y - 5}}}, "
        "stack={name='iron-plate', count=3}}\n"
        f"S.create_entity{{name='small-biter-corpse', position={{{lo.x - 8}, {lo.y - 8}}}}}"
    )
    before = clear.count_entities(rcon, area)
    occ_before = rt.occupied(area)
    cleared_tiles = clear.entity_tiles(rcon, area, clear.CLEARABLE_TYPES)
    assert before.get("tree", 0) > 0 and before.get("item-entity", 0) >= 1
    assert before.get("corpse", 0) >= 1
    out = clear.clear_obstacles(rcon, area)
    for t in clear.CLEARABLE_TYPES:
        assert out[t] == before.get(t, 0), t
    assert out["total"] == sum(before.get(t, 0) for t in clear.CLEARABLE_TYPES)
    after = clear.count_entities(rcon, area)
    assert not set(after) & set(clear.CLEARABLE_TYPES)
    # resources, cliffs and anything else untouched
    for k, v in before.items():
        if k not in clear.CLEARABLE_TYPES:
            assert after.get(k) == v, k
    occ_after = rt.occupied(area)
    kept = {k for k in before if k not in clear.CLEARABLE_TYPES and k != "resource"}
    kept_tiles = clear.entity_tiles(rcon, area, tuple(kept)) if kept else frozenset()
    assert occ_after == kept_tiles
    assert occ_after == occ_before - (cleared_tiles - kept_tiles)
    # a second pass finds nothing
    assert clear.clear_obstacles(rcon, area)["total"] == 0
