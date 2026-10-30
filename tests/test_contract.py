"""Phase 6a task 2: contract fields (entity, per-lane and on-demand rates, port groups,
port space) — filled by the generators and fixtures, read by the router, placement and
fixtures instead of looking at unit layouts (no server)."""

from __future__ import annotations

from dataclasses import replace

import pytest

import fgen.route.belt as rbelt
import fgen.site.place as place
from fgen.gen.base import Infeasible
from fgen.gen.mining_patch import MiningPatchParams
from fgen.gen.mining_patch import generate as gen_mining
from fgen.gen.smelting_row import SmeltingRowParams
from fgen.gen.smelting_row import generate as gen_smelting
from fgen.geom import belt as beltgeom
from fgen.geom import footprint
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import (
    PORT_LEAD,
    SIDE_LOAD_STUB,
    Interface,
    LaneMaterial,
    Layout,
    PlacedUnit,
    Placement,
    PortGroup,
    PortSpace,
    Pose,
    RateProfile,
    Unit,
)
from fgen.layout.validate import check_contract, validate_unit
from fgen.route.connect import connect
from fgen.route.power import power_poles
from fgen.sim import harness as hs
from fgen.site.model import Site, link
from fgen.terrain import paint
from fgen.terrain.model import Terrain, patches

from .route_helpers import belt_line, flat, ore_and_coal_input

E, W, N = Direction.E, Direction.W, Direction.N


# ---- model ------------------------------------------------------------------------


def test_rate_profile_fixed_and_on_demand():
    rp = RateProfile.fixed(7.5, {"left": 5.0, "right": 2.5}, depletion_s_lower_bound=100.0)
    assert rp.supply == "fixed" and rp.initial == 7.5 and rp.limit is None
    assert rp.lane("left") == 5.0 and rp.lane("right") == 2.5
    assert rp.depletion_s_lower_bound == 100.0
    od = RateProfile.on_demand(15.0, {"left": 7.5, "right": 7.5})
    assert od.supply == "on_demand" and od.initial is None and od.limit == 15.0
    i = Interface("item", "iron-ore", Tile(0, 0), E, "both", 7.5, "output", rate_profile=rp)
    assert i.fixed_rate() == 7.5 and not i.on_demand()
    assert i.lane_rate("left") == 5.0 and i.lane_rate("right") == 2.5
    src = replace(i, rate=None, rate_profile=od)
    assert src.on_demand() and src.fixed_rate() is None and src.lane_rate("left") == 7.5
    # without a per-lane split a one-lane port carries everything on its lane
    one = Interface("item", "coal", Tile(0, 0), E, "right", 0.5, "output")
    assert one.lane_rate("right") == 0.5 and one.lane_rate("left") == 0.0
    both = Interface("item", "coal", Tile(0, 0), E, "both", 0.5, "output")
    assert both.lane_rate("left") is None  # unknown split


def test_port_space_conventions():
    assert PortSpace.item("direct") == PortSpace("direct", 1 + PORT_LEAD)
    assert PortSpace.item("side").upstream == SIDE_LOAD_STUB + PORT_LEAD
    assert PortSpace.item("both").upstream == SIDE_LOAD_STUB + PORT_LEAD
    assert rbelt.STUB == SIDE_LOAD_STUB


def test_mirror_swaps_lane_rates_and_group_requirements():
    i = Interface(
        "item",
        "iron-ore",
        Tile(0, 0),
        E,
        "left",
        5.0,
        "input",
        rate_profile=RateProfile.fixed(5.0, {"left": 5.0}),
        entity="transport-belt",
        port_group="feed",
        space=PortSpace.item("both"),
    )
    u = Unit(
        Layout([Placement("transport-belt", Tile(0, 0).center(), E)]),
        [i],
        {},
        (PortGroup("feed", (LaneMaterial("left", "iron-ore"),)),),
    )
    w = PlacedUnit("u", u, Tile(3, 3), Pose(N, mirror=True)).world()
    (m,) = w.interfaces
    assert m.lane == "right" and m.rate_profile.lanes == (("right", 5.0),)
    assert m.entity == "transport-belt" and m.port_group == "feed" and m.space == i.space
    assert w.group("feed").lane_materials() == {"right": "iron-ore"}
    assert i.world(Tile(0, 0), Pose(N, mirror=True)).rate_profile.lane("right") == 5.0


# ---- generators and fixtures fill the fields -----------------------------------


def _patch(shape):
    t = Terrain.from_resources(paint.to_resources(shape, "iron-ore"))
    (p,) = patches(t, "iron-ore")
    return p, t


@pytest.mark.parametrize("axis", ["x", "y"])
def test_mining_contract_fields(gd, axis):
    patch, terrain = _patch(paint.l_shape(0, 0, 24, 20, 8, 1000))
    p = MiningPatchParams(axis=axis)
    unit = gen_mining(p, gd, patch, terrain)
    assert check_contract(unit, gd) == []
    outs = [i for i in unit.interfaces if i.kind == "item"]
    assert len({i.port_group for i in outs}) == len(outs)  # one belt, one group each
    assert {g.name for g in unit.groups} == {i.port_group for i in outs}
    for i, b in zip(outs, unit.meta["belts"], strict=True):
        assert i.entity == p.belt and i.space == PortSpace.item("direct")
        rp = i.rate_profile
        assert rp.supply == "fixed" and rp.initial == i.rate
        assert dict(rp.lanes) == {
            ln: n * unit.meta["drill_rate"] for ln, n in b["lanes"].items() if n
        }
        assert rp.depletion_s_lower_bound is not None
    (power,) = [i for i in unit.interfaces if i.kind == "power"]
    assert power.entity == p.pole


@pytest.mark.parametrize("sides", [1, 2])
def test_smelting_contract_fields(gd, sides):
    p = SmeltingRowParams(target_rate=5.0, sides=sides)
    unit = gen_smelting(p, gd)
    assert check_contract(unit, gd) == []
    ins = [i for i in unit.interfaces if i.kind == "item" and i.role == "input"]
    outs = [i for i in unit.interfaces if i.kind == "item" and i.role == "output"]
    assert len({i.port_group for i in ins}) == 1  # ore and fuel share the input belt
    assert len({i.port_group for i in outs}) == len(outs) == sides
    for i in ins + outs:
        assert i.entity == p.belt and i.space is not None
    assert all(i.space.approach == "both" for i in ins)
    assert all(i.space.approach == "direct" for i in outs)
    for o in outs:
        assert o.rate_profile.lanes == ((o.lane, o.rate),)


def test_fixture_contract_fields(gd):
    lane = beltgeom.lane_items_per_second(gd, "fast-transport-belt")
    src = hs.source_unit(gd, "iron-ore", "fast-transport-belt")
    (o,) = src.interfaces
    assert o.on_demand() and o.rate is None and o.entity == "fast-transport-belt"
    assert o.rate_profile.limit == beltgeom.items_per_second(gd, "fast-transport-belt")
    assert o.lane_rate("left") == o.lane_rate("right") == lane
    snk = hs.sink_unit(gd, "iron-plate")
    assert snk.interfaces[0].on_demand() and snk.interfaces[0].space.approach == "direct"
    for pole_name in ("small-electric-pole", "substation"):
        pw = hs.power_unit(gd, pole_name)
        assert pw.interfaces[0].entity == pole_name
    for u in (src, snk, hs.power_unit(gd), hs.fluid_source_unit(gd, "water")):
        assert check_contract(u, gd) == []


# ---- contract invariants ------------------------------------------------------------


def test_contract_invariants(gd):
    good = belt_line(gd, 3, role="output", rate=5.0)
    assert check_contract(good, gd) == []
    (i,) = good.interfaces

    def codes(**change) -> set[str]:
        u = replace(good, interfaces=[replace(i, **change)])
        return {v.code for v in check_contract(u, gd)}

    assert codes(entity="fast-transport-belt") == {"contract.entity"}
    assert codes(entity=None) == {"contract.entity"}
    assert codes(space=None) == {"contract.space"}
    assert codes(space=PortSpace.item("side")) == {"contract.space"}  # lane both
    assert codes(rate_profile=RateProfile.fixed(4.0)) == {"contract.rate"}
    assert codes(rate_profile=RateProfile.fixed(5.0, {"left": 1.0, "right": 1.0})) == {
        "contract.rate"
    }
    assert codes(rate_profile=RateProfile.on_demand()) == {"contract.rate"}  # rate 5.0
    one = replace(i, lane="left", buffer_items=beltgeom.run_items(gd, 3, "left"))
    u = replace(
        good, interfaces=[replace(one, rate_profile=RateProfile.fixed(5.0, {"right": 5.0}))]
    )
    assert {v.code for v in check_contract(u, gd)} == {"contract.rate"}
    assert codes(buffer_items=None) == {"contract.buffer"}
    assert codes(buffer_items=i.buffer_items + 1) == {"contract.buffer"}
    assert codes(lane="left") == {"contract.buffer"}  # same run, half the lanes
    assert codes(port_group="nope") == {"contract.group"}
    # a group spans one port tile
    two = replace(i, tile=Tile(1, 0))
    u = replace(good, interfaces=[i, two])
    assert "contract.group" in {v.code for v in check_contract(u, gd)}
    # validate_unit includes the contract checks
    bad = replace(good, interfaces=[replace(i, entity=None)])
    assert "contract.entity" in {v.code for v in validate_unit(bad, gd)}


# ---- the router reads the contract, not the layouts ---------------------------------


def test_no_layout_lookups_left():
    assert not hasattr(rbelt, "belt_at")
    assert not hasattr(place, "PORT_CLEARANCE")
    assert not hasattr(rbelt, "RESERVE")


def test_consumer_belt_comes_from_the_contract(gd):
    """The stub uses the belt the consumer's contract names (here deliberately not the
    belt in its layout), so the router cannot have looked at the layout."""
    ore = belt_line(gd, 3, role="output", rate=5.0)
    dst = ore_and_coal_input(gd)
    dst = replace(
        dst, interfaces=[replace(i, entity="fast-transport-belt") for i in dst.interfaces]
    )
    site = Site(
        flat(-10, -15, 30, 15),
        [PlacedUnit("ore", ore, Tile(-5, -8)), PlacedUnit("row", dst, Tile(10, 0))],
        [link(("ore", 0), ("row", 0))],
    )
    conn = connect(site, gd)
    assert {p.name for p in conn.extra} == {"fast-transport-belt"}


def test_port_group_not_tile_makes_one_consumer(gd):
    ore = belt_line(gd, 3, role="output", material="iron-ore", rate=5.0)
    coal = belt_line(gd, 3, role="output", material="coal", rate=0.5)
    dst = ore_and_coal_input(gd)
    units = [PlacedUnit("ore", ore, Tile(-5, -8)), PlacedUnit("coal", coal, Tile(-5, 8))]
    links = [link(("ore", 0), ("row", 0)), link(("coal", 0), ("row", 1))]
    site = Site(flat(-10, -15, 30, 15), [*units, PlacedUnit("row", dst, Tile(10, 0))], links)
    conn = connect(site, gd)
    assert len(conn.extra) == SIDE_LOAD_STUB  # one stub for the group
    assert {c["interface"] for c in conn.checks if c["predicate"].startswith("LaneMaterial")} == {
        "row/feed"
    }
    # the same two ports without a group are two consumers: two stubs on one tile
    split = replace(
        dst,
        interfaces=[replace(i, port_group=None) for i in dst.interfaces],
        groups=(),
    )
    site = Site(flat(-10, -15, 30, 15), [*units, PlacedUnit("row", split, Tile(10, 0))], links)
    with pytest.raises(Infeasible):
        connect(site, gd)


def test_per_lane_rates_are_checked(gd):
    """A direct link keeps the producer's lanes: 9/s on one lane of a yellow belt is too
    much even though the whole belt (10/s) fits; without the split the old 'half on each
    lane' estimate would have passed."""
    lane = beltgeom.lane_items_per_second(gd, "transport-belt")
    src = belt_line(gd, 3, role="output", rate=lane + 2.5)
    (o,) = src.interfaces
    skew = RateProfile.fixed(lane + 2.5, {"left": lane + 1.5, "right": 1.0})
    src = replace(src, interfaces=[replace(o, rate_profile=skew)])
    dst = belt_line(gd, 6, role="input", rate=None)
    site = Site(
        flat(-10, -15, 30, 15),
        [PlacedUnit("a", src, Tile(-5, 0)), PlacedUnit("b", dst, Tile(10, 0))],
        [link(("a", 0), ("b", 0))],
    )
    with pytest.raises(Infeasible, match="left lane"):
        connect(site, gd)
    even = RateProfile.fixed(lane + 2.5, {"left": (lane + 2.5) / 2, "right": (lane + 2.5) / 2})
    site.units[0] = PlacedUnit(
        "a", replace(src, interfaces=[replace(o, rate_profile=even)]), Tile(-5, 0)
    )
    site = Site(site.terrain, site.units, site.links)
    assert connect(site, gd).violations == []


def test_rate_none_is_not_on_demand(gd):
    """Only ``RateProfile.on_demand`` supplies whatever is taken: a producer with no rate
    and no profile supplies nothing the MinRate check can count."""
    dst = ore_and_coal_input(gd, ore_rate=5.0)
    od = belt_line(gd, 3, role="output", rate=None)  # helper: rate None -> on_demand
    assert od.interfaces[0].on_demand()
    plain = replace(od, interfaces=[replace(od.interfaces[0], rate_profile=None)])
    for unit, ok in ((od, True), (plain, False)):
        site = Site(
            flat(-10, -15, 30, 15),
            [PlacedUnit("ore", unit, Tile(-5, -8)), PlacedUnit("row", dst, Tile(10, 0))],
            [link(("ore", 0), ("row", 0))],
        )
        conn = connect(site, gd)
        (mr,) = [
            c
            for c in conn.checks
            if c["predicate"].startswith("MinRate") and c["interface"] == "row#0"
        ]
        assert mr["ok"] is ok


def test_approach_decides_the_link_mode():
    base = Interface("item", "iron-ore", Tile(0, 0), E, "left", 5.0, "input")
    assert rbelt.link_mode(replace(base, space=PortSpace.item("both"))) == ("side", "left")
    assert rbelt.link_mode(replace(base, space=PortSpace.item("side"))) == ("side", "left")
    assert rbelt.link_mode(replace(base, space=PortSpace.item("direct"))) == ("direct", None)
    both = replace(base, lane="both")
    assert rbelt.link_mode(replace(both, space=PortSpace.item("both"))) == ("direct", None)
    with pytest.raises(Infeasible):
        rbelt.link_mode(replace(both, space=PortSpace.item("side")))
    with pytest.raises(Infeasible):
        rbelt.link_mode(replace(base, space=PortSpace("side", SIDE_LOAD_STUB - 1)))
    with pytest.raises(Infeasible):
        rbelt.link_mode(base)  # no space at all


def test_placement_keeps_the_contract_space_free():
    out = Interface("item", "x", Tile(0, 0), E, "both", 1.0, "output", space=PortSpace("direct", 4))
    assert place.port_tiles(out) == [Tile(k, 0) for k in range(1, 5)]
    inp = replace(out, role="input", space=PortSpace.item("side"))
    assert place.port_tiles(inp) == [Tile(-k, 0) for k in range(1, inp.space.upstream + 1)]
    assert place.port_tiles(out, 1) == [Tile(1, 0)]  # an explicit override
    with pytest.raises(ValueError):
        place.port_tiles(replace(out, space=None))


def test_power_poles_from_the_contract(gd):
    for name in ("small-electric-pole", "substation"):
        pw = hs.power_unit(gd, name)
        site = Site(flat(-10, -10, 10, 10), [PlacedUnit("p", pw, Tile(0, 0))], [])
        ((_, got, c),) = power_poles(site, gd)
        (pole_pl,) = [q for q in pw.layout.placements if q.name == name]
        assert got == name and c == pole_pl.position
        assert footprint.center_at_tile(gd, name, pole_pl.position.floor()) == pole_pl.position
    bad = replace(pw, interfaces=[replace(pw.interfaces[0], entity=None)])
    site = Site(flat(-10, -10, 10, 10), [PlacedUnit("p", bad, Tile(0, 0))], [])
    with pytest.raises(rbelt.RouteError):
        power_poles(site, gd)


# ---- buffer_items (phase 8) ----------------------------------------------------------


def _belts(gd, spec: list[tuple[str, int, int, Direction, str | None]]):
    out = []
    for name, x, y, d, kind in spec:
        extra = {"type": kind} if kind else {}
        out.append(Placement(name, Tile(x, y).center(), d, extra))
    return out


def _port(gd, tile, role, lane, ps, **kw):
    return Unit(
        Layout(ps),
        [
            Interface(
                "item",
                "iron-plate",
                tile,
                Direction.E,
                lane,
                1.0,
                role,
                entity="transport-belt",
                space=PortSpace.item("direct"),
                **kw,
            )
        ],
    )


def test_buffer_items_counts_the_belt_run_behind_the_port(gd):
    from fgen.layout.belt_run import run_tiles

    E = Direction.E
    ug = "underground-belt"
    # input: the port tile to the end of the belt, an underground pair counts its two
    # ends, not the gap; the tail past the last pickup counts too
    ps = _belts(
        gd,
        [("transport-belt", 0, 0, E, None), (ug, 1, 0, E, "input"), (ug, 4, 0, E, "output")]
        + [("transport-belt", x, 0, E, None) for x in range(5, 8)],
    )
    assert run_tiles(Layout(ps), gd, Tile(0, 0), "input") == 6
    assert run_tiles(Layout(ps), gd, Tile(5, 0), "input") == 3
    per = beltgeom.run_items(gd, 1, "both")
    assert beltgeom.run_items(gd, 6, "left") == pytest.approx(6 * per / 2)
    ok = _port(gd, Tile(0, 0), "input", "left", ps, buffer_items=beltgeom.run_items(gd, 6, "left"))
    assert not [v for v in check_contract(ok, gd) if v.code == "contract.buffer"]
    short = replace(ok, interfaces=[replace(ok.interfaces[0], buffer_items=per)])
    assert {v.code for v in check_contract(short, gd)} >= {"contract.buffer"}
    # output: only from the first tile an inserter drops onto
    belt = [("transport-belt", x, 1, E, None) for x in range(6)]
    ins = Placement("inserter", Tile(2, 0).center(), Direction.N)  # drops on (2, 1)
    lay = Layout([*_belts(gd, belt), ins])
    assert run_tiles(lay, gd, Tile(5, 1), "output") == 4
    out = _port(gd, Tile(5, 1), "output", "right", [])
    out = replace(out, layout=lay)
    out = replace(
        out,
        interfaces=[replace(out.interfaces[0], buffer_items=beltgeom.run_items(gd, 4, "right"))],
    )
    assert not [v for v in check_contract(out, gd) if v.code == "contract.buffer"]
    # no dropper at all (loader-fed belt): the whole belt
    assert run_tiles(Layout(_belts(gd, belt)), gd, Tile(5, 1), "output") == 6


def test_generators_and_fixtures_fill_buffer_items(gd):
    from fgen.gen.smelting_row import FLOW
    from fgen.sim import harness as hs

    # smelting row: the input belt is as long as the row; the output belt starts at the
    # first furnace's inserter column (computed from the layout, not from the generator)
    for sides in (1, 2):
        row = gen_smelting(SmeltingRowParams(target_rate=7.5, sides=sides), gd)
        belts = [p for p in row.layout.placements if gd.entity(p.name).type == "transport-belt"]
        ins = [p for p in row.layout.placements if gd.entity(p.name).type == "inserter"]
        for i in row.interfaces:
            if i.kind != "item":
                continue
            line = [p for p in belts if p.position.floor().y == i.tile.y]
            if i.role == "input":
                n = len(line)
            else:
                first = min(
                    p.position.floor().x
                    for p in ins
                    if p.position.floor().y in (i.tile.y - 1, i.tile.y + 1)
                )
                n = len([p for p in line if p.position.floor().x >= first])
            assert i.buffer_items == pytest.approx(beltgeom.run_items(gd, n, i.lane)), i
    assert FLOW == Direction.E
    # fixtures: one belt tile each
    for u in (hs.source_unit(gd, "iron-plate"), hs.sink_unit(gd, "iron-plate")):
        (i,) = u.interfaces
        assert i.buffer_items == pytest.approx(beltgeom.run_items(gd, 1, "both"))
        assert check_contract(u, gd) == []
