"""M2 (phase 3a): grid A* and belt routing on hand-written terrain (no server)."""

from __future__ import annotations

import pytest

from fgen.gen.base import Infeasible
from fgen.geom import belt as beltgeom
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import PlacedUnit
from fgen.layout.render import to_ascii
from fgen.route.astar import NoPath, astar
from fgen.route.belt import RouteError
from fgen.route.connect import connect
from fgen.site.model import Site, link

from .route_helpers import belt_line, flat, ore_and_coal_input, rect

E, S, W, N = Direction.E, Direction.S, Direction.W, Direction.N


def _render(site, conn, gd):
    from fgen.terrain.model import background

    return to_ascii(conn.site_layout(site), gd, rulers=True, background=background(site.terrain))


def _types(gd, conn):
    return [gd.entity(p.name).type for p in conn.layout.placements]


def test_astar_turn_cost_and_no_path():
    # 4-neighbour grid with a wall; states are tiles
    wall = {Tile(2, y) for y in range(-5, 5)}

    def expand(t):
        for d in (N, E, S, W):
            n = t.neighbor(d)
            if n not in wall and -6 <= n.x <= 6 and -6 <= n.y <= 6:
                yield n, 1.0, n

    def h(t):
        return abs(t.x - 4) + abs(t.y)

    p = astar([Tile(0, 0)], lambda t: t == Tile(4, 0), expand, h)
    assert p.actions[-1] == Tile(4, 0) and p.cost == len(p.actions)
    assert all(t not in wall for t in p.actions)
    wall |= {Tile(2, y) for y in range(-6, 7)}
    with pytest.raises(NoPath) as exc:
        astar([Tile(0, 0)], lambda t: t == Tile(4, 0), expand, h)
    assert exc.value.closest.x == 1  # pressed against the wall


def test_direct_route_around_water(gd):
    src = belt_line(gd, 3, role="output")
    dst = belt_line(gd, 3, role="input")
    lake = rect(8, -6, 20, 6)  # 13 wide: wider than an underground belt can jump
    terrain = flat(-5, -15, 35, 15, water=lake)
    site = Site(
        terrain,
        [PlacedUnit("a", src, Tile(0, 0)), PlacedUnit("b", dst, Tile(26, 0))],
        [link(("a", 0), ("b", 0))],
    )
    conn = connect(site, gd)
    assert conn.violations == [], _render(site, conn, gd)
    tiles = conn.layout.tiles(gd)
    assert not tiles & lake
    assert "underground-belt" not in _types(gd, conn)
    # the last belt flows into the consumer from behind
    last = [p for p in conn.layout.placements if p.position.floor() == Tile(25, 0)]
    assert last and last[0].direction == E
    assert conn.ok


def test_underground_over_cliff(gd):
    src = belt_line(gd, 2, role="output")
    dst = belt_line(gd, 2, role="input")
    cliff = rect(8, -30, 9, 30)  # a 2-wide wall from edge to edge: only a jump crosses it
    terrain = flat(-5, -30, 25, 30, occupied=cliff)
    site = Site(
        terrain,
        [PlacedUnit("a", src, Tile(0, 0)), PlacedUnit("b", dst, Tile(16, 0))],
        [link(("a", 0), ("b", 0))],
    )
    conn = connect(site, gd)
    assert conn.violations == [], _render(site, conn, gd)
    ugs = [p for p in conn.layout.placements if gd.entity(p.name).type == "underground-belt"]
    assert len(ugs) == 2
    a, b = sorted(ugs, key=lambda p: p.position.x)
    assert a.extra["type"] == "input" and b.extra["type"] == "output"
    assert a.direction == b.direction == E
    gap = b.position.x - a.position.x
    assert gap + 1 > 2 and gap <= beltgeom.underground_partner_range(gd, a.name)
    assert not conn.layout.tiles(gd) & cliff


def test_cliff_too_wide_is_infeasible(gd):
    src = belt_line(gd, 2, role="output")
    dst = belt_line(gd, 2, role="input")
    reach = beltgeom.underground_partner_range(gd, "underground-belt")
    cliff = rect(5, -20, 5 + reach, 20)  # reach tiles wide: the exit would need offset reach+1
    terrain = flat(-5, -20, 30, 20, occupied=cliff)
    site = Site(
        terrain,
        [PlacedUnit("a", src, Tile(0, 0)), PlacedUnit("b", dst, Tile(20, 0))],
        [link(("a", 0), ("b", 0), "ore")],
    )
    with pytest.raises(RouteError) as exc:
        connect(site, gd)
    assert exc.value.link == "ore" and exc.value.where is not None
    assert exc.value.where.x < 5  # stuck on the near side of the cliff


def test_two_sources_side_load_left_and_right(gd):
    ore = belt_line(gd, 3, role="output", material="iron-ore", rate=5.0)
    coal = belt_line(gd, 3, role="output", material="coal", rate=0.6)
    dst = ore_and_coal_input(gd)
    terrain = flat(-10, -15, 30, 15)
    site = Site(
        terrain,
        [
            PlacedUnit("ore", ore, Tile(-5, -8)),
            PlacedUnit("coal", coal, Tile(-5, 8)),
            PlacedUnit("row", dst, Tile(10, 0)),
        ],
        [link(("ore", 0), ("row", 0)), link(("coal", 0), ("row", 1))],
    )
    conn = connect(site, gd)
    assert conn.violations == [], _render(site, conn, gd)
    # a two-tile stub west of the consumer, side-loaded at its east tile
    stub = {p.position.floor() for p in conn.extra}
    assert stub == {Tile(9, 0), Tile(8, 0)}
    by_tile = {p.position.floor(): p for p in conn.layout.placements}
    left_side = beltgeom.lane_side(E, "left")  # north
    right_side = beltgeom.lane_side(E, "right")
    assert by_tile[Tile(9, 0).neighbor(left_side)].direction == left_side.opposite
    assert by_tile[Tile(9, 0).neighbor(right_side)].direction == right_side.opposite
    routes = {r.link: r for r in conn.routes}
    assert routes["ore#0->row#0"].lane == "left" and routes["coal#0->row#1"].lane == "right"
    lm = [c for c in conn.checks if c["predicate"].startswith("LaneMaterial")]
    assert len(lm) == 2 and all(c["ok"] for c in lm)
    assert all(c["ok"] for c in conn.checks)


def test_wrong_material_on_lane_is_infeasible(gd):
    copper = belt_line(gd, 3, role="output", material="copper-ore", rate=5.0)
    dst = ore_and_coal_input(gd)
    site = Site(
        flat(-10, -15, 30, 15),
        [PlacedUnit("cu", copper, Tile(-5, -8)), PlacedUnit("row", dst, Tile(10, 0))],
        [link(("cu", 0), ("row", 0))],
    )
    with pytest.raises(Infeasible, match="must carry only iron-ore"):
        connect(site, gd)


def test_lane_capacity_is_checked(gd):
    cap = beltgeom.lane_items_per_second(gd, "transport-belt")
    a = belt_line(gd, 3, role="output", rate=cap * 0.6)
    b = belt_line(gd, 3, role="output", rate=cap * 0.6)
    dst = ore_and_coal_input(gd)
    site = Site(
        flat(-10, -15, 30, 15),
        [
            PlacedUnit("a", a, Tile(-5, -8)),
            PlacedUnit("b", b, Tile(-5, -4)),
            PlacedUnit("row", dst, Tile(10, 0)),
        ],
        [link(("a", 0), ("row", 0)), link(("b", 0), ("row", 0))],
    )
    with pytest.raises(Infeasible, match="left lane") as exc:
        connect(site, gd)
    assert any("fast-transport-belt" in s for s in exc.value.suggestions)


def test_min_rate_shortfall_is_a_warning(gd):
    ore = belt_line(gd, 3, role="output", rate=2.0)
    dst = ore_and_coal_input(gd, ore_rate=5.0)
    site = Site(
        flat(-10, -15, 30, 15),
        [PlacedUnit("ore", ore, Tile(-5, -8)), PlacedUnit("row", dst, Tile(10, 0))],
        [link(("ore", 0), ("row", 0))],
    )
    conn = connect(site, gd)
    mr = [c for c in conn.checks if c["predicate"].startswith("MinRate")]
    assert mr[0]["ok"] is False and any("needs 5" in w for w in conn.warnings)
    assert not conn.ok


def test_unreachable_consumer(gd):
    src = belt_line(gd, 2, role="output")
    dst = belt_line(gd, 2, role="input")
    moat = rect(10, -10, 30, 10) - rect(17, -1, 23, 1)  # island far beyond any jump
    terrain = flat(-5, -15, 35, 15, water=moat)
    site = Site(
        terrain,
        [PlacedUnit("a", src, Tile(0, 0)), PlacedUnit("b", dst, Tile(20, 0))],
        [link(("a", 0), ("b", 0))],
    )
    with pytest.raises(RouteError, match="no belt path"):
        connect(site, gd)


def test_route_avoids_inserter_reach_and_foreign_belts(gd):
    from fgen.layout.model import Layout, Placement, Unit

    src = belt_line(gd, 2, role="output")
    dst = belt_line(gd, 2, role="input")
    # an N-facing inserter at (5,-1) drops onto (5,0); a stray belt at (8,1) pushes
    # north into (8,0): both on the straight line between the units
    ins = Unit(Layout([Placement("inserter", Tile(5, -1).center(), N)]), [], {})
    stray = Unit(Layout([Placement("transport-belt", Tile(8, 1).center(), N)]), [], {})
    site = Site(
        flat(-5, -10, 25, 10),
        [
            PlacedUnit("a", src),
            PlacedUnit("b", dst, Tile(14, 0)),
            PlacedUnit("ins", ins),
            PlacedUnit("stray", stray),
        ],
        [link(("a", 0), ("b", 0))],
    )
    conn = connect(site, gd)
    tiles = conn.layout.tiles(gd)
    assert Tile(5, 0) not in tiles and Tile(5, -2) not in tiles
    assert Tile(8, 0) not in tiles
    # only the stray entities themselves are flagged
    own = conn.layout.tiles(gd)
    assert all(not (v.tiles & own) for v in conn.violations), _render(site, conn, gd)
    stray_codes = {"inserter.pickup_empty", "inserter.drop_empty", "power.uncovered"}
    assert {v.code for v in conn.violations} <= stray_codes | {"belt.discontinuous"}


def test_mirrored_consumer_swaps_lane_requirement(gd):
    from fgen.layout.model import Pose

    ore = belt_line(gd, 3, role="output", material="iron-ore", rate=5.0)
    dst = ore_and_coal_input(gd)
    placed = PlacedUnit("row", dst, Tile(20, 0), Pose(N, mirror=True))
    w = placed.world()
    assert w.interfaces[0].direction == W and w.interfaces[0].lane == "right"
    assert w.group("feed").lane_materials() == {"right": "iron-ore", "left": "coal"}
    site = Site(
        flat(-10, -15, 40, 15),
        [PlacedUnit("ore", ore, Tile(30, -8)), placed],
        [link(("ore", 0), ("row", 0))],
    )
    conn = connect(site, gd)
    assert not [v for v in conn.violations if v.code != "inserter.pickup_empty"]
    (r,) = conn.routes
    assert r.lane == "right"
    # west-flowing consumer: its right lane is the north side
    side_tile = Tile(19, 0).neighbor(W.opposite)  # stub tile next to the consumer
    by_tile = {p.position.floor(): p for p in conn.layout.placements}
    approach = side_tile.neighbor(beltgeom.lane_side(W, "right"))
    assert approach == side_tile.neighbor(N)
    assert by_tile[approach].direction == S
