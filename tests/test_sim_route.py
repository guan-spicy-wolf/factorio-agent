"""M3 (phase 3a), headless: routed power is one network; a routed pipe fills a tank;
side-loading onto underground belt ends (the VERIFY check behind the router's stub)."""

from __future__ import annotations

import pytest

from fgen.gen.smelting_row import SmeltingRowParams
from fgen.gen.smelting_row import generate as gen_smelting
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import PlacedUnit
from fgen.route.connect import connect
from fgen.sim import harness as hs
from fgen.sim import lua, site_measure
from fgen.sim.terrain import RconTerrain
from fgen.site.model import Site, link
from fgen.site.place import place_port

pytestmark = pytest.mark.headless

W = Direction.W


def _snapshot(server, area, water=()):
    rt = RconTerrain(server.rcon)
    rt.generate(area)
    server.rcon.lua_json(lua.clear_area(((area[0].x, area[0].y), (area[1].x + 1, area[1].y + 1))))
    if water:
        rt.set_tiles(water, "water")
    return rt.snapshot(area, generate=False)


def _restore(server, tiles):
    RconTerrain(server.rcon).set_tiles(tiles, "grass-1")


def test_power_chain_is_one_network(server, gd):
    area = (Tile(-40, -30), Tile(70, 70))
    lake = [Tile(x, y) for x in range(-10, 60) for y in range(12, 25)]
    snap = _snapshot(server, area, lake)
    try:
        row = gen_smelting(SmeltingRowParams(target_rate=2.5), gd)
        site = Site(
            snap,
            [
                PlacedUnit("a", row, Tile(0, 0)),
                PlacedUnit("b", row, Tile(0, 40)),
                PlacedUnit("pw", hs.power_unit(gd), Tile(-20, 30)),
            ],
        )
        conn = connect(site, gd)
        assert not [v for v in conn.violations if v.code.startswith("power.")]
        site_measure.deploy(server, gd, site, conn)
        nets = site_measure.networks(server, site_measure.site_area(site, conn, gd))
        assert len(nets["pole_networks"]) == 1, nets
        assert len(nets["consumer_networks"]) == 1 and nets["unpowered"] == 0, nets
        n_poles = sum(nets["pole_networks"].values())
        assert n_poles == sum(
            1
            for p in conn.site_layout(site).placements
            if gd.entity(p.name).type == "electric-pole"
        )
    finally:
        server.rcon.lua_json(lua.clear_area(((area[0].x, area[0].y), (area[1].x, area[1].y))))
        _restore(server, lake)


def test_pipe_fills_tank_across_water(server, gd):
    area = (Tile(100, -30), Tile(140, 30))
    strip = [Tile(x, y) for x in range(108, 111) for y in range(-30, 31)]
    snap = _snapshot(server, area, strip)
    try:
        site = Site(
            snap,
            [
                PlacedUnit("src", hs.fluid_source_unit(gd, "water"), Tile(102, 0)),
                place_port("tank", hs.fluid_sink_unit(gd, "water"), 0, Tile(120, 3), W),
            ],
            [link(("src", 0), ("tank", 0))],
        )
        conn = connect(site, gd)
        assert conn.violations == []
        assert any(p.name == "pipe-to-ground" for p in conn.layout.placements)
        site_measure.deploy(server, gd, site, conn)
        server.wait_ticks(300)
        got = site_measure.tank_fluid(server, gd, site, "tank")
        assert got["found"] and got["fluid"] == "water" and got["amount"] > 0, got
    finally:
        server.rcon.lua_json(lua.clear_area(((area[0].x, area[0].y), (area[1].x, area[1].y))))
        _restore(server, strip)


SIDE_LOAD = """
local function put(n, x, y, d, t)
  local sp = {name=n, position={x+0.5, y+0.5}, direction=d, force=F}
  if t then sp.type = t end
  return S.create_entity(sp)
end
local E, SO = defines.direction.east, defines.direction.south
local feeds = {plain=9, entrance=3, exit=6}
local k = 0
for what, fx in pairs(feeds) do
  local y0 = 200 + k * 10
  k = k + 1
  for x = 0, 2 do put('transport-belt', x, y0, E) end
  put('underground-belt', 3, y0, E, 'input')
  put('underground-belt', 6, y0, E, 'output')
  for x = 7, 12 do put('transport-belt', x, y0, E) end
  put('transport-belt', fx, y0 - 1, SO)
  put('transport-belt', fx, y0 - 2, SO)
  put('loader', fx, y0 - 3.5, SO, 'output')
  local ch = S.create_entity{name='infinity-chest', position={fx+0.5, y0-4.5}, force=F}
  ch.infinity_container_filters = {{index=1, name='iron-plate', count=50, mode='exactly'}}
  storage['side_' .. what] = y0
end
"""


def test_side_load_onto_underground_ends_fills_near_lane(server):
    """Router never side-loads onto undergrounds (it builds its own stub), but VERIFY
    wants the behaviour: on 2.1.23 a belt pointing into the side of an east-flowing
    underground entrance or exit from the north fills the left (near) lane, as on a
    plain belt."""
    server.rcon.lua(lua.PRELUDE + SIDE_LOAD)
    try:
        server.wait_ticks(900)
        out = server.rcon.lua_json(
            lua.PRELUDE
            + "local out = {}\n"
            + "for _, w in ipairs({'plain', 'entrance', 'exit'}) do\n"
            + "  local y0 = storage['side_' .. w]\n"
            + "  local e = S.find_entity('transport-belt', {12.5, y0 + 0.5})\n"
            + "  out[w] = {e.get_transport_line(defines.transport_line.left_line).get_item_count(),"
            + " e.get_transport_line(defines.transport_line.right_line).get_item_count()}\n"
            + "end\n"
            + lua.emit("out")
        )
        for w in ("plain", "entrance", "exit"):
            left, right = out[w]
            assert left > 0 and right == 0, (w, out)
    finally:
        server.rcon.lua_json(lua.clear_area(((-2, 190), (16, 232))))


CLIFFS = (
    "west-to-east",
    "north-to-south",
    "west-to-north",
    "north-to-east",
    "east-to-south",
    "south-to-west",
)


def test_snapshot_marks_every_cliff_tile_poles_cannot_use(server):
    """Poles (and belts, pipes) next to cliffs: every tile where ``can_place_entity``
    refuses them is in the snapshot's ``occupied`` (diagonal cliffs have rotated
    bounding boxes; the snapshot takes the box of the rotated corners)."""
    rcon = server.rcon
    rcon.lua(
        lua.PRELUDE
        + f"for k, o in ipairs({lua.lit(list(CLIFFS))}) do\n"
        + "  S.create_entity{name='cliff', position={260, 60 + k * 12}, cliff_orientation=o}\n"
        + "end"
    )
    area = (Tile(250, 60), Tile(275, 140))
    try:
        occ = RconTerrain(rcon).occupied(area)
        for name in ("small-electric-pole", "medium-electric-pole", "transport-belt", "pipe"):
            res = rcon.lua_json(
                lua.PRELUDE
                + "local out = {}\n"
                + "for x = 250, 275 do for y = 60, 140 do\n"
                + f"  if not S.can_place_entity{{name={lua.lit(name)}, "
                + "position={x + 0.5, y + 0.5}, force=F} then table.insert(out, {x, y}) end\n"
                + "end end\n"
                + lua.emit("out")
            )
            rows = res.values() if isinstance(res, dict) else res
            blocked = {Tile(int(x), int(y)) for x, y in rows}
            assert blocked and blocked <= occ, (name, sorted(blocked - occ)[:5])
    finally:
        rcon.lua_json(lua.clear_area(((250, 60), (276, 141))))
