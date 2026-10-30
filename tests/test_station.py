"""Supplier rule, shared materials through a splitter, multi-output flows (plan-phase8
task A); hand-made units and painted terrain, no solver."""

from __future__ import annotations

import pytest

from fgen.data.runtime import RuntimeState
from fgen.gen import junction
from fgen.gen import site_sweep as ss
from fgen.gen.direct_chain import DirectChainParams
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import (
    Interface,
    Layout,
    MinRate,
    PlacedUnit,
    Placement,
    PortGroup,
    PortSpace,
    RateProfile,
    Unit,
)
from fgen.layout.validate import validate_unit
from fgen.site.build import SitePlan, plan_and_connect
from fgen.site.model import Endpoint, Link
from fgen.terrain import paint
from fgen.terrain.model import Terrain, patches

from .route_helpers import belt_line, flat

E = Direction.E
FAKE = "test/chain"


def test_supplier_family_reads_the_game_data(gd):
    pt = {"iron-ore": None}
    assert ss.supplier_family(gd, "iron-ore", pt) == (ss.MINE, "iron-ore")
    assert ss.supplier_family(gd, "copper-ore", pt) is None  # no patch: a fixture
    assert ss.supplier_family(gd, "iron-plate", pt) == (ss.ROW, "iron-plate")
    assert ss.supplier_family(gd, "electronic-circuit", pt) == (ss.CHAIN, "electronic-circuit")
    assert ss.supplier_family(gd, "logistic-science-pack", pt) == (
        ss.CHAIN,
        "logistic-science-pack",
    )
    assert ss.supplier_family(gd, "coal", pt) is None
    assert ss.supplier_family(gd, "water", pt) is None


def test_chain_inputs_and_demand_counts(gd):
    green = ss.GREEN_CHAIN
    assert ss.chain_inputs(green, gd) == ["electronic-circuit", "iron-plate"]
    circuit = DirectChainParams(recipe="electronic-circuit")
    assert ss.chain_inputs(circuit, gd) == ["iron-plate", "copper-plate"]
    spec = ss.StationSpec("logistic-science-pack", 1.0, {"iron-ore": None, "copper-ore": None}, 300)
    spec.chains[ss.GREEN_RECIPE] = green
    counts = ss.demand_counts(spec, gd)
    # iron plates are wanted by the green chain and the circuit chain; everything else once
    assert counts == {
        "electronic-circuit": 1,
        "iron-plate": 2,
        "copper-plate": 1,
        "iron-ore": 1,
        "copper-ore": 1,
    }


def fake_chain(gd, inputs: list[tuple[str, float]], product: str, rate: float) -> Unit:
    """A chain stand-in: one input belt per material (MinRate = rate x share) and one
    output belt, all flowing east; family ``test/chain`` with a flat capacity."""
    ps, ifaces = [], []
    for y, (m, share) in enumerate(inputs):
        ps += [Placement("transport-belt", Tile(x, y).center(), E) for x in range(4)]
        ifaces.append(
            Interface(
                "item",
                m,
                Tile(0, y),
                E,
                "both",
                rate * share,
                "input",
                requires=(MinRate(rate * share),),
                entity="transport-belt",
                port_group=f"in{y}",
                space=PortSpace.item("direct"),
            )
        )
    y = len(inputs)
    ps += [Placement("transport-belt", Tile(x, y).center(), E) for x in range(4)]
    ifaces.append(
        Interface(
            "item",
            product,
            Tile(3, y),
            E,
            "both",
            rate,
            "output",
            rate_profile=RateProfile.fixed(rate),
            entity="transport-belt",
            port_group="out",
            space=PortSpace.item("direct"),
        )
    )
    meta = {
        "family": FAKE,
        "belt_terminals": [Tile(3, k) for k in range(len(inputs))],
        "params": {"recipe": product, "target_rate": rate},
        "rate": rate,
    }
    groups = (*(PortGroup(f"in{k}") for k in range(len(inputs))), PortGroup("out"))
    return Unit(Layout(ps), ifaces, meta, groups)


@pytest.fixture
def fake_chains(gd, monkeypatch):
    """``gen_chain`` replaced by :func:`fake_chain` with the green station's shares
    (per pack: 4.5 iron plates and 1 circuit; per circuit: 1 iron and 1.5 copper)."""
    shares = {
        "logistic-science-pack": [("electronic-circuit", 1.0), ("iron-plate", 4.5)],
        "electronic-circuit": [("iron-plate", 1.0), ("copper-plate", 1.5)],
    }

    def gen(p, gd_):
        return fake_chain(gd_, shares[p.recipe], p.recipe, p.target_rate)

    monkeypatch.setattr(ss, "gen_chain", gen)
    ss.register_capacity(FAKE, lambda u, gd_, rs: {"throughput": u.meta["rate"]})
    yield
    ss.CAPACITY.pop(FAKE, None)


def painted(gd):
    res = {}
    res.update(paint.to_resources(paint.rectangle(0, 0, 20, 16, 3000), "iron-ore"))
    res.update(paint.to_resources(paint.rectangle(70, 10, 16, 16, 3000), "copper-ore"))
    terrain = Terrain.from_resources(res, margin=60)
    return terrain, {
        "iron-ore": patches(terrain, "iron-ore")[0],
        "copper-ore": patches(terrain, "copper-ore")[0],
    }


def test_station_plan_sizes_one_iron_row_for_two_consumers_and_splits_it(gd, fake_chains):
    terrain, pt = painted(gd)
    plan = ss.green_science_plan(gd, terrain, pt, 1.0, window_s=300)
    free = dict(plan.free)
    assert {"science", "electronic-circuit", "iron-row", "copper-row", "iron-split"} <= set(free)
    assert {u.name for u in plan.fixed} == {"iron-mine", "copper-mine"}
    # one iron row for both chains: 4.5 + 1.0 plates/s; the copper row for the circuits
    assert free["iron-row"].meta["params"]["target_rate"] == pytest.approx(5.5)
    assert free["copper-row"].meta["params"]["target_rate"] == pytest.approx(1.5)
    sp = free["iron-split"]
    assert sp.meta["family"] == junction.FAMILY
    assert validate_unit(sp, gd) == []
    assert sp.interfaces[0].min_rate() == pytest.approx(5.5)
    labels = [ln.label for ln in plan.links]
    assert "iron-plate" in labels  # row -> splitter
    assert {"iron-plate>science", "iron-plate>electronic-circuit"} <= set(labels)
    # the whole station places and routes without violations
    row, conn = ss.plan_row("test", plan, gd)
    assert row["connection"]["violations"] == [] and conn.ok
    assert row["placement"][-1]["error"] is None
    # junction ports are not judged (no force counter for a pass-through)
    assert not any(p["port"].startswith("iron-split") for p in row["ports"])
    site = ss.plan_and_connect(plan, gd)[0]
    pred = ss.predict(site, gd, RuntimeState())
    assert pred["science#2"] == pytest.approx(1.0)
    assert pred["iron-row#2"] == pytest.approx(5.5)
    assert pred["iron-split#1"] + pred["iron-split#2"] == pytest.approx(5.5)
    assert pred["copper-row#2"] == pytest.approx(1.5)
    assert pred["iron-mine#0"] == pytest.approx(5.5)


def test_red_station_is_the_same_plan_as_before(gd, fake_chains):
    """The generic rule reproduces the phase-7 red-science plan: the same unit names
    and the same order of units and links (placement and routing depend on it)."""
    terrain, pt = painted(gd)
    ss.register_capacity(FAKE, lambda u, gd_, rs: {"throughput": u.meta["rate"]})
    import fgen.gen.site_sweep as mod

    mod_gen = mod.gen_chain

    def red(p, gd_):
        return fake_chain(
            gd_, [("copper-plate", 1.0), ("iron-plate", 2.0)], p.recipe, p.target_rate
        )

    mod.gen_chain = red
    try:
        plan = ss.red_science_plan(gd, terrain, pt, 1.0, window_s=300)
    finally:
        mod.gen_chain = mod_gen
    assert [n for n, _ in plan.free] == [
        "science",
        "copper-row",
        "copper-row.coal",
        "power",
        "iron-row",
        "iron-row.coal",
        "science.automation-science-pack",
    ]
    assert [u.name for u in plan.fixed] == ["copper-mine", "iron-mine"]
    assert [ln.label for ln in plan.links] == [
        "copper-ore",
        "copper-plate",
        "copper-row.coal",
        "power",
        "iron-ore",
        "iron-plate",
        "iron-row.coal",
        "science.automation-science-pack",
    ]


def test_allocate_is_water_filling():
    assert ss.allocate(10.0, [3.0, 4.0]) == [3.0, 4.0]
    assert ss.allocate(5.0, [3.0, 4.0]) == [2.5, 2.5]
    assert ss.allocate(5.0, [1.0, 7.0]) == [1.0, 4.0]
    assert ss.allocate(15.0, [1.0]) == [1.0]


def test_splitter_unit_routes_one_producer_to_two_consumers(gd):
    src = belt_line(gd, 4, role="output", rate=5.0, material="iron-plate")
    sp = junction.splitter_unit(gd, "iron-plate", src.interfaces[0], (2.0, 3.0))
    assert validate_unit(sp, gd) == []
    assert [i.role for i in sp.interfaces] == ["input", "output", "output"]
    assert sp.interfaces[0].min_rate() == pytest.approx(5.0)
    assert junction.predict(sp, gd)["throughput"] == pytest.approx(
        2 * __import__("fgen.geom.belt", fromlist=["x"]).lane_items_per_second(gd, "transport-belt")
    )
    plan = SitePlan(
        flat(-30, -30, 60, 60),
        fixed=[PlacedUnit("src", src)],
        free=[
            ("j", sp),
            ("a", belt_line(gd, 5, role="input", rate=2.0, material="iron-plate")),
            ("b", belt_line(gd, 5, role="input", rate=3.0, material="iron-plate")),
        ],
        links=[
            Link(Endpoint("src", 0), Endpoint("j", 0), "in"),
            Link(Endpoint("j", 1), Endpoint("a", 0), "a"),
            Link(Endpoint("j", 2), Endpoint("b", 0), "b"),
        ],
    )
    _, conn, log = plan_and_connect(plan, gd)
    assert conn.violations == [] and conn.ok and log[-1]["error"] is None
    assert {r.link for r in conn.routes} == {"in", "a", "b"}


def test_choose_mining_rounds_the_cap_up_to_whole_drills(gd):
    """A consumer asking 0.75/s from 0.5/s drills gets two drills (1.0/s capped), not
    one: the phase-8 green 0.5 station starved its copper row on the old rule."""
    from fgen.gen.mining_patch import generate as gen_mining
    from fgen.geom import mining as mining_geom

    res = paint.to_resources(paint.rectangle(0, 0, 20, 16, 5000), "copper-ore")
    terrain = Terrain.from_resources(res, margin=20)
    patch = patches(terrain, "copper-ore")[0]
    per_drill = mining_geom.drill_rate(gd, "electric-mining-drill", "copper-ore")
    mp = ss.choose_mining(gd, terrain, patch, max_rate=1.5 * per_drill, window_s=300)
    assert mp.max_belt_rate == pytest.approx(2 * per_drill)
    unit = gen_mining(mp, gd, patch, terrain)
    assert unit.meta["anchor"]["rate"] == pytest.approx(2 * per_drill)
    # an exact multiple is unchanged
    mp2 = ss.choose_mining(gd, terrain, patch, max_rate=2 * per_drill, window_s=300)
    assert mp2.max_belt_rate == pytest.approx(2 * per_drill)
