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
    assert modes["row.iron-ore"] == ("side", "left") and modes["row.coal"] == ("side", "right")
    assert modes["row.iron-plate"] == ("direct", None)
    assert {p["port"] for p in row["ports"]} == {"row#0", "row#1", "row#2"}
    jsonl, md = ss.write(tmp_path, [row], gd)
    measured = {**row, "measured": {"x": 1}, "judged": {"ports": []}}
    jsonl.write_text(json.dumps(measured) + "\n")
    # re-planning the same (unchanged) site keeps the measurement
    ss.write(tmp_path, [row], gd)
    assert json.loads(jsonl.read_text())["measured"] == {"x": 1}
    assert "## flat" in md.read_text()


def test_fill_time_reads_the_contract_and_the_routes(gd):
    """Phase 8: the buffer of a link is the producer's output ``buffer_items``, the
    route's belt tiles and the consumer's input ``buffer_items``; fill_time never looks
    at a unit layout."""
    import inspect

    from fgen.data.runtime import RuntimeState
    from fgen.geom import belt as beltgeom
    from fgen.terrain import paint
    from fgen.terrain.model import patches

    assert "layout" not in inspect.getsource(ss.fill_time)
    res = paint.to_resources(paint.rectangle(0, 0, 16, 16, 3000), "iron-ore")
    terrain = Terrain.from_resources(res, margin=40)
    patch = patches(terrain, "iron-ore")[0]
    site, conn, _ = ss.plan_and_connect(ss.real_map_plan(gd, terrain, patch), gd)
    rs = RuntimeState(mining_drill_productivity_bonus=0.3)
    ft = ss.fill_time(site, conn, gd, rs)
    (ore,) = [d for d in ft["links"] if d["link"] == "ore"]
    ln = next(x for x in site.links if x.label == "ore")
    src, dst = site.interface(ln.src), site.interface(ln.dst)
    route = next(r for r in conn.routes if r.link == "ore")
    tiles = sum(
        1
        for p in route.placements
        if gd.entity(p.name).type in ("transport-belt", "underground-belt")
    )
    per_tile = gd.constant("belt_items_per_tile")
    # mine port: both lanes; the row's ore port: the left lane only, fed from both
    # lanes, so the row's belt holds one lane's worth
    want_in = dst.buffer_items
    want = src.buffer_items + tiles * per_tile * beltgeom.lane_share(gd, src.lane) + want_in
    assert ore["buffer_items"] == round(want)
    assert ore["seconds"] == round(want / (ore["supply"] - ore["drawn"]))
    assert ft["seconds"] >= ore["seconds"] > 0
    assert ft["link"] == "ore"
    # a flat site has no surplus anywhere: nothing to fill
    flat_site = ss.flat_site(gd, Terrain(buildable=area_tiles(ss.FLAT_AREA)))
    from fgen.route.connect import connect

    assert ss.fill_time(flat_site, connect(flat_site, gd), gd, rs)["seconds"] == 0.0
