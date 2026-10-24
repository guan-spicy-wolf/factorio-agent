"""M3 (phase 3a): pole chains and pipe routing on hand-written terrain (no server)."""

from __future__ import annotations

import pytest

from fgen.gen.smelting_row import SmeltingRowParams
from fgen.gen.smelting_row import generate as gen_smelting
from fgen.geom import pole
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Layout, PlacedUnit, Placement, Unit
from fgen.route.belt import RouteError
from fgen.route.connect import connect
from fgen.route.power import mst
from fgen.sim import harness as hs
from fgen.site.model import Site, link
from fgen.site.place import place_port

from .route_helpers import flat, rect

E, S, W, N = Direction.E, Direction.S, Direction.W, Direction.N


def _poles(gd, layout):
    return [p for p in layout.placements if gd.entity(p.name).type == "electric-pole"]


def test_mst_is_spanning():
    from fgen.geom.vec import Vec2

    pts = [Vec2(0, 0), Vec2(10, 0), Vec2(0, 10), Vec2(30, 30)]
    edges = mst(pts)
    assert len(edges) == 3 and (0, 1) in edges and (0, 2) in edges


def test_pole_chain_across_a_lake_joins_three_units(gd):
    row = gen_smelting(SmeltingRowParams(target_rate=2.5), gd)
    lake = rect(-10, 12, 60, 24)  # wires cross water, poles cannot stand on it
    terrain = flat(-30, -20, 70, 60, water=lake)
    power = hs.power_unit(gd)
    site = Site(
        terrain,
        [
            PlacedUnit("a", row, Tile(0, 0)),
            PlacedUnit("b", row, Tile(0, 40)),
            PlacedUnit("pw", power, Tile(-20, 30)),
        ],
    )
    conn = connect(site, gd)
    assert not [v for v in conn.violations if v.code.startswith("power.")]
    chain = _poles(gd, conn.layout)
    assert chain and not (conn.layout.tiles(gd) & lake)
    # every chain pole is in reach of some other pole: the whole site is one network
    allp = [(p.name, p.position) for p in _poles(gd, conn.site_layout(site))]
    assert len(pole.copper_tree(gd, allp)) == len(allp) - 1
    assert [r.kind for r in conn.routes] == ["power", "power"]


def test_pole_chain_unreachable_island(gd):
    power = hs.power_unit(gd)
    row = gen_smelting(SmeltingRowParams(target_rate=2.5), gd)
    reach = pole.wire_distance(gd, "small-electric-pole", "small-electric-pole")
    w = int(reach) + 4
    moat = rect(-w - 4, -w - 4, w + 4, w + 4) - rect(-3, -3, 3, 3)
    terrain = flat(-40, -40, 80, 40, water=moat)
    site = Site(terrain, [PlacedUnit("pw", power, Tile(-1, -1)), PlacedUnit("a", row, Tile(20, 0))])
    with pytest.raises(RouteError, match="no pole chain"):
        connect(site, gd)


def _fluid_site(gd, terrain, gap_at=10, foreign=None):
    src = hs.fluid_source_unit(gd, "water")
    snk = hs.fluid_sink_unit(gd, "water")
    units = [
        PlacedUnit("src", src, Tile(0, 0)),
        place_port("tank", snk, 0, Tile(2 * gap_at, 0), W),
    ]
    if foreign is not None:
        units.append(foreign)
    return Site(terrain, units, [link(("src", 0), ("tank", 0))])


def test_pipe_with_underground_over_water(gd):
    strip = rect(8, -30, 10, 30)  # 3 wide, edge to edge: only pipe-to-ground crosses
    site = _fluid_site(gd, flat(-5, -30, 30, 30, water=strip))
    conn = connect(site, gd)
    assert conn.violations == []
    (r,) = conn.routes
    p2g = [p for p in r.placements if p.name == "pipe-to-ground"]
    assert len(p2g) == 2
    a, b = sorted(p2g, key=lambda p: p.position.x)
    assert a.direction == W and b.direction == E  # normal connections face the path
    assert not conn.layout.tiles(gd) & strip
    assert r.fluid == "water" and all(f == "water" for f in conn.route_fluids().values())


def test_pipe_too_wide_gap(gd):
    reach = int(gd.entity("pipe-to-ground").max_underground_distance)
    strip = rect(6, -30, 6 + reach, 30)  # reach tiles wide: the exit would need offset reach+1
    site = _fluid_site(gd, flat(-5, -30, 40, 30, water=strip), gap_at=15)
    with pytest.raises(RouteError, match="no pipe path"):
        connect(site, gd)


def test_pipe_keeps_away_from_foreign_fluid(gd):
    # a crude-oil pipe right next to the straight line between source and tank
    oil = Unit(
        Layout([Placement("pipe", Tile(x, 1).center()) for x in range(3, 15)]),
        [],
        {"fluids": {k: "crude-oil" for k in range(12)}},
    )
    site = _fluid_site(gd, flat(-5, -15, 30, 15), foreign=PlacedUnit("oil", oil))
    conn = connect(site, gd)
    assert not [v for v in conn.violations if v.code == "fluid.unintended_adjacency"]
    oil_tiles = {Tile(x, 1) for x in range(3, 15)}
    for p in conn.layout.placements:
        if p.name == "pipe":
            t = p.position.floor()
            assert all(t.neighbor(d) not in oil_tiles for d in (N, E, S, W)), t
    # and the validator does flag a pipe that touches it
    bad = Layout([*conn.site_layout(site).placements, Placement("pipe", Tile(3, 0).center())])
    from fgen.layout.validate import validate

    fl = site.fluids()
    n = len(site.merged().placements)
    fl.update({n + k: f for k, f in conn.route_fluids().items()})
    fl[len(bad.placements) - 1] = "water"
    assert any(v.code == "fluid.unintended_adjacency" for v in validate(bad, gd, fluids=fl))
