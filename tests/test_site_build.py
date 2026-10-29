"""Link-driven placement and site building (plan-phase7 task A), hand-written terrain."""

from __future__ import annotations

import pytest

from fgen.geom.vec import Direction, Tile
from fgen.layout.model import PlacedUnit, Pose
from fgen.site import place
from fgen.site.build import SitePlan, build, order, plan_and_connect
from fgen.site.model import Endpoint, Link
from fgen.terrain import paint
from fgen.terrain.model import Terrain, area_tiles, patches

from .route_helpers import belt_line, flat, ore_and_coal_input

E, S, W, N = Direction.E, Direction.S, Direction.W, Direction.N
C = place.PlaceCosts()


def test_estimate_orders_straight_before_bends_before_detours():
    straight = place.estimate(Tile(0, 0), E, Tile(10, 0), E, C)
    s_bend = place.estimate(Tile(0, 0), E, Tile(10, 3), E, C)
    one_turn = place.estimate(Tile(0, 0), E, Tile(10, 3), S, C)  # arrives moving south
    wrong_side = place.estimate(Tile(0, 0), E, Tile(10, -3), S, C)
    behind = place.estimate(Tile(0, 0), E, Tile(-10, 0), E, C)
    u_turn = place.estimate(Tile(0, 0), E, Tile(10, 0), W, C)
    assert straight == 10 * C.step
    assert straight < one_turn < s_bend < wrong_side
    assert straight < u_turn < behind


def test_link_ends_follow_the_consumer_contract(gd):
    src = belt_line(gd, 3, role="output").interfaces[0]  # port (2,0) facing E
    direct = belt_line(gd, 3, role="input").interfaces[0].world(Tile(10, 0))
    start, din, goal, dgoal = place.link_ends(src, direct)
    assert (start, din) == (Tile(3, 0), E)
    assert (goal, dgoal) == (Tile(9, 0), E)  # the tile before the port, moving its way
    side = ore_and_coal_input(gd).interfaces[0].world(Tile(10, 0))  # ore on the left lane
    _, _, goal, dgoal = place.link_ends(src, side)
    assert (goal, dgoal) == (Tile(9, -1), S)  # north of the stub tile, flowing south


def test_candidates_put_the_consumer_straight_ahead_and_off_the_ore(gd):
    from fgen.layout.transform import place_unit
    from fgen.site.build import free_fn

    producer = PlacedUnit("p", belt_line(gd, 3, role="output"))
    consumer = belt_line(gd, 12, role="input")
    ends = [place.LinkEnd(0, producer.world().interfaces[0], False)]

    def pick(terrain):
        free = free_fn(terrain, [producer], gd)
        return next(iter(place.candidates(consumer, gd, free, terrain, ends, poses=[Pose()])))

    terrain = flat(-10, -10, 40, 10)
    cost, origin, pose = pick(terrain)
    # both ports keep 2 tiles free (the producer's 3 and 4, the consumer's own two): the
    # nearest legal origin is x = 7, straight ahead, three belts
    assert (origin, pose) == (Tile(7, 0), Pose())
    assert cost == pytest.approx(C.step * 3)
    ore = paint.to_resources(paint.rectangle(4, -3, 10, 7, 500), "iron-ore")
    ored = Terrain.from_resources(ore, area=(Tile(-10, -10), Tile(40, 10)))
    cost2, origin2, _ = pick(ored)
    tiles = place_unit(consumer, origin2, Pose()).layout.tiles(gd)
    assert not any(ored.resource_at(t) for t in tiles)  # a longer belt beats 9 ore tiles
    assert cost2 > cost


def test_order_is_flow_order_then_fixtures(gd):
    mine = PlacedUnit("mine", belt_line(gd, 4, role="output"))
    row_a = belt_line(gd, 5, role="input")
    row_b = belt_line(gd, 5, role="input")
    chain = ore_and_coal_input(gd)
    sink = belt_line(gd, 2, role="input", rate=None)
    sink_unit = type(sink)(sink.layout, sink.interfaces, {**sink.meta, "fixture": {}}, sink.groups)
    plan = SitePlan(
        flat(-30, -30, 60, 60),
        fixed=[mine],
        free=[("sink", sink_unit), ("chain", chain), ("b", row_b), ("a", row_a)],
        links=[
            Link(Endpoint("mine", 0), Endpoint("a", 0)),
            Link(Endpoint("mine", 0), Endpoint("b", 0)),
            Link(Endpoint("a", 0), Endpoint("chain", 0)),
            Link(Endpoint("b", 0), Endpoint("chain", 1)),
            Link(Endpoint("chain", 0), Endpoint("sink", 0)),
        ],
    )
    seq = order(plan, gd)
    assert set(seq[:2]) == {"a", "b"} and seq[2:] == ["chain", "sink"]


def test_build_places_two_producers_onto_two_lanes_of_one_belt(gd):
    """Two fixed-rate producers (real units, not fixtures) feed the left and right lane of
    the same consumer belt; the builder places them, the router side-loads both."""
    ore = PlacedUnit("ore", belt_line(gd, 4, role="output", material="iron-ore", rate=5.0))
    coal = belt_line(gd, 4, role="output", material="coal", rate=0.5, lane="left")
    consumer = ore_and_coal_input(gd)
    plan = SitePlan(
        flat(-40, -40, 60, 40),
        fixed=[ore],
        free=[("coal", coal), ("row", consumer)],
        links=[
            Link(Endpoint("ore", 0), Endpoint("row", 0), "ore"),
            Link(Endpoint("coal", 0), Endpoint("row", 1), "coal"),
        ],
    )
    site, conn, log = plan_and_connect(plan, gd)
    assert conn.violations == [] and conn.ok and log[-1]["error"] is None
    modes = {r.link: (r.mode, r.lane) for r in conn.routes}
    assert modes == {"ore": ("side", "left"), "coal": ("side", "right")}
    assert all(c["ok"] for c in conn.checks if c["ok"] is not None)
    # no hand-written anchors: the row sits right past the ore belt's end
    row = site.placed("row")
    assert row.world().interfaces[0].tile.x > ore.world().interfaces[0].tile.x


def test_plan_and_connect_moves_a_unit_whose_link_cannot_be_routed(gd):
    """The cheapest spot lies past a wall too thick for an underground belt; the only
    other spot needs a detour. The first attempt fails to route, the second moves the
    consumer to its next candidate."""
    maxd = int(gd.entity("underground-belt").max_distance)
    land: set[Tile] = set()
    land |= area_tiles((Tile(-2, -1), Tile(4, 1)))  # around the producer (too short to fit)
    far = 5 + maxd
    land |= area_tiles((Tile(far, -1), Tile(far + 5, 1)))  # exactly one spot past the wall
    land |= area_tiles((Tile(3, 0), Tile(4, 9)))  # corridor down to the pocket
    land |= area_tiles((Tile(2, 7), Tile(12, 9)))  # the pocket (a detour away)
    terrain = Terrain(buildable=frozenset(land))
    producer = PlacedUnit("p", belt_line(gd, 3, role="output"))
    plan = SitePlan(
        terrain,
        fixed=[producer],
        free=[("c", belt_line(gd, 3, role="input"))],
        links=[Link(Endpoint("p", 0), Endpoint("c", 0), "ore")],
        poses=(Pose(),),
    )
    first = build(plan, gd).placed("c")
    assert first.origin == Tile(far + 2, 0)  # cheapest by the estimate
    site, conn, log = plan_and_connect(plan, gd)
    assert [x["moved"] for x in log] == ["c", None]
    assert site.placed("c").origin.y == 8 and conn.violations == []


def test_anchorless_plan_starts_at_the_origin(gd):
    consumer = belt_line(gd, 6, role="input", rate=None)
    producer = belt_line(gd, 3, role="output")
    plan = SitePlan(
        flat(-20, -20, 30, 20),
        free=[("src", producer), ("row", consumer)],
        links=[Link(Endpoint("src", 0), Endpoint("row", 0))],
    )
    site = build(plan, gd)
    assert site.placed("row").origin == Tile(0, 0)  # the largest unit first, at the origin
    assert site.placed("src").origin != Tile(0, 0)


@pytest.mark.skipif(
    not __import__("fgen.solve.cpsat", fromlist=["available"]).available(),
    reason="needs OR-Tools (uv run --extra solver)",
)
def test_red_science_plan_on_painted_patches(gd):
    """Two painted patches -> mines sized to the rows -> rows sized to the chain -> the
    chain; placed from the links and routed without violations; the prediction gives
    the target at every stage. A short solver time box keeps the test fast."""
    from fgen.data.runtime import RuntimeState
    from fgen.gen import site_sweep as ss
    from fgen.gen.direct_chain import DirectChainParams

    res = {}
    res.update(paint.to_resources(paint.rectangle(0, 0, 20, 16, 3000), "iron-ore"))
    res.update(paint.to_resources(paint.rectangle(60, 10, 16, 16, 3000), "copper-ore"))
    terrain = Terrain.from_resources(res, margin=50)
    pt = {
        "iron-plate": patches(terrain, "iron-ore")[0],
        "copper-plate": patches(terrain, "copper-ore")[0],
    }
    chain = DirectChainParams(recipe="automation-science-pack", time_limit=5)
    plan = ss.red_science_plan(gd, terrain, pt, 1.0, window_s=300, chain=chain)
    names = {n for n, _ in plan.free}
    assert {"science", "iron-row", "copper-row"} <= names
    assert {u.name for u in plan.fixed} == {"iron-mine", "copper-mine"}
    row, conn = ss.plan_row("test", plan, gd)
    assert row["connection"]["violations"] == [] and conn.ok
    assert all(x["error"] is None for x in row["placement"][-1:])
    # sizing from the contracts: the rows' targets are the chain's MinRates, the mines
    # deliver what the rows need (within one drill's rate)
    assert row["params"]["iron-row"]["target_rate"] == pytest.approx(2.0)
    assert row["params"]["copper-row"]["target_rate"] == pytest.approx(1.0)
    ports = {p["port"]: p for p in row["ports"]}
    assert ports["iron-mine#0"]["initial"] == pytest.approx(2.0)
    assert ports["copper-mine#0"]["initial"] == pytest.approx(1.0)
    site = ss.plan_and_connect(plan, gd)[0]
    pred = ss.predict(site, gd, RuntimeState(mining_drill_productivity_bonus=0.3))
    assert pred["science#2"] == pytest.approx(1.0)
    assert pred["iron-row#2"] == pytest.approx(2.0) and pred["iron-mine#0"] == pytest.approx(2.0)
    assert pred["copper-row#2"] == pytest.approx(1.0)
    # no literal coordinates: every free unit was placed by the rule
    assert all(x["origin"] != [0, 0] for x in row["site"]["units"] if "mine" not in x["name"])
    assert not conn.warnings  # the chain's internal gear lane raises no "nothing arrives"
