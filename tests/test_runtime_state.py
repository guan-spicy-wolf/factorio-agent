"""Phase 4b task B: RuntimeState (force research state) in the predictions."""

from __future__ import annotations

import pytest

from fgen.data.runtime import NO_RESEARCH, RuntimeState
from fgen.gen import throughput as tp
from fgen.gen.mining_sweep import predicted
from fgen.geom import mining

STATE = RuntimeState(
    inserter_stack_size_bonus=3,
    bulk_inserter_capacity_bonus=11,
    belt_stack_size_bonus=3,
    mining_drill_productivity_bonus=0.3,
    source="force",
)


def test_default_is_no_research(gd):
    assert RuntimeState.from_json(None) == NO_RESEARCH
    assert RuntimeState.from_json(NO_RESEARCH.to_json()) == NO_RESEARCH
    assert RuntimeState.from_json({**STATE.to_json(), "unknown": 1}) == STATE
    base = gd.constant("inserter_base_hand_size")
    assert tp.inserter_hand_size(gd, "fast-inserter") == base


def test_hand_bonus_per_kind(gd):
    base = gd.constant("inserter_base_hand_size")
    assert tp.inserter_hand_size(gd, "fast-inserter", STATE) == base + 3
    assert tp.inserter_hand_size(gd, "bulk-inserter", STATE) == base + 11
    one = tp.inserter_throughput_estimate(gd, "inserter")
    assert tp.inserter_throughput_estimate(gd, "inserter", STATE) == pytest.approx(one * (base + 3))


def test_mining_prediction_productivity_and_stacks(gd):
    rate = mining.drill_rate(gd, "electric-mining-drill", "iron-ore")
    cap = tp.belt_lane_capacity(gd, "transport-belt")
    assert tp.drill_output_rate(gd, "electric-mining-drill", "iron-ore", STATE) == pytest.approx(
        rate * 1.3
    )
    n = int(cap // rate)  # a full lane without research
    c = {
        "drill_rate": rate,
        "lane_capacity": cap,
        "lanes": [{"left": 2, "right": n}],
        "drill_stacks": False,
    }
    assert predicted(c, NO_RESEARCH) == [pytest.approx(2 * rate + n * rate)]
    # productivity raises the short lane, the full lane stays at capacity
    assert predicted(c, STATE) == [pytest.approx(2 * rate * 1.3 + min(n * rate * 1.3, cap))]
    # drills dropping full belt stacks fill a lane with stacks of 1 + bonus
    stacked = {**c, "drill_stacks": True}
    assert predicted(stacked, STATE) == [pytest.approx(2 * rate * 1.3 + n * rate * 1.3)]


def test_smelting_prediction_reads_recipe_productivity(gd):
    """The smelting row's prediction takes the force's recipe productivity through the
    path the direct chain uses (``tp.machine_rate`` / ``tp.productivity_factor``):
    more products per craft, the same ore and fuel per craft, the belts unchanged."""
    from fgen.gen.smelting_row import SmeltingRowParams, generate

    bonus = 0.1
    rs = RuntimeState(recipe_productivity=(("iron-plate", bonus),), source="force")
    f, r = "stone-furnace", gd.recipe("iron-plate")
    base = gd.entity(f).crafting_speed * r.results[0][1] / r.energy_required
    assert tp.productivity_factor("iron-plate", rs) == 1 + bonus
    assert tp.productivity_factor("copper-plate", rs) == 1
    assert tp.machine_rate(gd, "iron-plate", f) == pytest.approx(base)
    assert tp.machine_rate(gd, "iron-plate", f, rs) == pytest.approx(base * (1 + bonus))
    unit = generate(SmeltingRowParams(furnace=f, target_rate=7.5), gd)
    p0, p1 = tp.predict(unit, gd), tp.predict(unit, gd, rs)
    assert p0["runtime"] == NO_RESEARCH.to_json() and p1["runtime"] == rs.to_json()
    assert p0["machine_rate"] == pytest.approx(base)
    assert p1["machine_rate"] == pytest.approx(base * (1 + bonus))
    assert p1["limits"]["machines"] == pytest.approx(p0["machines"] * base * (1 + bonus))
    # a product takes 1 / (1 + bonus) of the ore and of the fuel: the same belts feed
    # that many more products; the output belt is the same belt
    for k in ("input:iron-ore", "fuel:coal"):
        assert p1["limits"][k] == pytest.approx(p0["limits"][k] * (1 + bonus))
    assert p1["limits"]["output_belt"] == p0["limits"]["output_belt"]
    # feeding inserters serve more products per item, taking inserters are unchanged
    assert p0["limits"]["inserters"] <= p1["limits"]["inserters"]
    assert p1["limits"]["inserters"] <= p0["limits"]["inserters"] * (1 + bonus) + 1e-9
    # a recipe without a bonus is unchanged by the state
    copper = generate(SmeltingRowParams(recipe="copper-plate", furnace=f, target_rate=7.5), gd)
    assert tp.predict(copper, gd, rs)["limits"] == tp.predict(copper, gd)["limits"]


def test_recipe_productivity_round_trip():
    rs = RuntimeState.from_json({"recipe_productivity": {"plastic-bar": 0.1}, "source": "force"})
    assert rs.productivity("plastic-bar") == 0.1 and rs.productivity("x") == 0.0
    assert RuntimeState.from_json(rs.to_json()) == rs
    assert "plastic-bar +10%" in rs.describe()
