"""Site scenarios, placement and the site characterization file (no server)."""

from __future__ import annotations

import itertools
import json

from fgen.gen import site_sweep as ss
from fgen.geom.vec import Direction, Tile
from fgen.sim import harness as hs
from fgen.site.place import find_spot, place_port, rings
from fgen.terrain.model import Terrain, area_tiles

from .route_helpers import flat, rect


def test_rings_are_nearest_first_and_complete():
    ts = list(rings(Tile(0, 0), 3))
    assert ts[0] == Tile(0, 0) and len(ts) == len(set(ts)) == 49
    assert all(
        max(abs(t.x), abs(t.y)) <= max(abs(u.x), abs(u.y)) for t, u in itertools.pairwise(ts)
    )


def test_place_port_and_find_spot(gd):
    src = hs.source_unit(gd, "iron-ore")
    for d in (Direction.N, Direction.E, Direction.S, Direction.W):
        pu = place_port("s", src, 0, Tile(5, 7), d)
        i = pu.interfaces_world()[0]
        assert i.tile == Tile(5, 7) and i.direction == d
    blocked = rect(-3, -3, 3, 3)
    terrain = flat(-20, -20, 20, 20, blocked=blocked)
    pu = find_spot("s", src, gd, terrain.can_build, Tile(0, 0))
    tiles = pu.world().layout.tiles(gd)
    assert not tiles & blocked and all(terrain.can_build(t) for t in tiles)


def test_fixture_units_are_valid(gd):
    from fgen.layout.validate import validate_unit

    for u in (
        hs.source_unit(gd, "coal", "fast-transport-belt"),
        hs.sink_unit(gd, "iron-plate"),
        hs.power_unit(gd, "medium-electric-pole"),
        hs.fluid_source_unit(gd, "water"),
        hs.fluid_sink_unit(gd, "water"),
    ):
        assert validate_unit(u, gd) == [], u.meta
    assert {
        p.name for p in hs.source_unit(gd, "coal", "fast-transport-belt").layout.placements
    } >= {
        "fast-loader",
        "fast-transport-belt",
    }


def test_flat_site_routes_cleanly_and_writes(gd, tmp_path):
    site = ss.flat_site(gd, Terrain(buildable=area_tiles(ss.FLAT_AREA)))
    row, conn = ss.plan_row(ss.FLAT, site, gd)
    assert conn.violations == [] and conn.ok
    modes = {r["link"]: (r["mode"], r["lane"]) for r in row["connection"]["routes"]}
    assert modes["ore"] == ("side", "left") and modes["coal"] == ("side", "right")
    assert modes["plates"] == ("direct", None)
    assert {p["port"] for p in row["ports"]} == {"row#0", "row#1", "row#2"}
    jsonl, md = ss.write(tmp_path, [row], gd)
    measured = {**row, "measured": {"x": 1}, "judged": {"ports": []}}
    jsonl.write_text(json.dumps(measured) + "\n")
    # re-planning the same (unchanged) site keeps the measurement
    ss.write(tmp_path, [row], gd)
    assert json.loads(jsonl.read_text())["measured"] == {"x": 1}
    assert "## flat" in md.read_text()
