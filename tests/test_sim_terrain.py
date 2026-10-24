"""M3 (phase 2): terrain read-back from headless Factorio and painted ore."""

from __future__ import annotations

import pytest

from fgen.geom.vec import Tile
from fgen.sim import lua
from fgen.sim.terrain import RconTerrain
from fgen.terrain import paint
from fgen.terrain.model import area_tiles, patches

pytestmark = pytest.mark.headless

AREA = (Tile(100, 100), Tile(139, 139))  # inside the verified flat field


@pytest.fixture(scope="module")
def rt(server):
    t = RconTerrain(server.rcon)
    t.generate(AREA)
    yield t
    server.rcon.lua_json(lua.clear_area(((90, 90), (150, 150))))
    t.clear_resources(AREA)
    t.set_tiles(area_tiles(AREA), "grass-1")


def test_flat_field_all_buildable(rt):
    snap = rt.snapshot(AREA)
    assert snap.buildable == area_tiles(AREA)
    assert snap.water == frozenset() and snap.occupied == frozenset()
    assert dict(snap.resources) == {}


def test_paint_and_read_back(rt):
    shape = paint.with_hole(paint.rectangle(104, 106, 12, 9, 777), 108, 109, 3, 2)
    shape.update(paint.blob(5, 40, 300, cx=128, cy=128, spread=50))
    res = paint.to_resources(shape, "iron-ore")
    res.update(paint.to_resources(paint.rectangle(120, 102, 3, 3, 55), "copper-ore"))
    assert rt.paint(res) == len(res)
    snap = rt.snapshot(AREA)
    assert dict(snap.resources) == res
    assert snap.buildable == area_tiles(AREA)  # ore tiles are buildable
    assert len(patches(snap, "iron-ore")) == 2
    # repainting replaces instead of stacking
    rt.paint({Tile(104, 106): ("iron-ore", 5)})
    assert rt.resources((Tile(104, 106), Tile(104, 106))) == {Tile(104, 106): ("iron-ore", 5)}
    assert rt.clear_resources(AREA) == len(res)
    assert rt.resources(AREA) == {}


def test_water_and_blocked(rt):
    lake = {Tile(x, y) for x in range(110, 116) for y in range(120, 124)}
    rt.set_tiles(lake, "water")
    deep = {Tile(130, 130), Tile(131, 130)}
    rt.set_tiles(deep, "deepwater")
    snap = rt.snapshot(AREA)
    assert snap.water == frozenset(lake | deep)
    assert snap.buildable == area_tiles(AREA) - lake - deep
    assert not rt.buildable(Tile(111, 121)) and rt.buildable(Tile(109, 121))
    rt.set_tiles(lake | deep, "grass-1")
    assert rt.water(AREA) == frozenset()


def test_occupied(rt, server):
    server.rcon.lua_json(
        lua.create_entities(
            [
                {"name": "steel-chest", "position": [105.5, 105.5]},
                {"name": "stone-furnace", "position": [110, 110]},
            ]
        )
    )
    occ = rt.occupied(AREA)
    assert occ == {Tile(105, 105), Tile(109, 109), Tile(110, 109), Tile(109, 110), Tile(110, 110)}
    assert not rt.buildable(Tile(105, 105))
    snap = rt.snapshot(AREA)
    assert not snap.can_build(Tile(110, 110)) and snap.can_build(Tile(111, 110))
    server.rcon.lua_json(lua.clear_area(((100, 100), (120, 120))))
    assert rt.occupied(AREA) == frozenset()
