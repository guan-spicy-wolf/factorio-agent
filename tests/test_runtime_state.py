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


def test_recipe_productivity_round_trip():
    rs = RuntimeState.from_json({"recipe_productivity": {"plastic-bar": 0.1}, "source": "force"})
    assert rs.productivity("plastic-bar") == 0.1 and rs.productivity("x") == 0.0
    assert RuntimeState.from_json(rs.to_json()) == rs
    assert "plastic-bar +10%" in rs.describe()
