"""Siting and placement questions and their answer files (plan-phase8 task A)."""

from __future__ import annotations

import json

import pytest

from fgen.site import decide
from fgen.site.build import ORDER_KEYS
from fgen.site.place import PlaceCosts
from fgen.terrain import paint
from fgen.terrain.model import Terrain


def two_iron_patches():
    res = {}
    res.update(paint.to_resources(paint.rectangle(10, 0, 12, 10, 500), "iron-ore"))  # near, poor
    res.update(paint.to_resources(paint.rectangle(60, 30, 20, 20, 5000), "iron-ore"))  # far, rich
    res.update(paint.to_resources(paint.rectangle(10, 40, 10, 10, 800), "copper-ore"))
    res.update(paint.to_resources(paint.rectangle(2, 2, 3, 3, 100), "iron-ore"))  # too small
    return Terrain.from_resources(res, margin=50)


def test_candidates_are_nearest_first_and_at_least_min_tiles():
    t = two_iron_patches()
    cands = decide.candidates(t, "iron-ore")
    assert [decide.patch_id(p) for p in cands] == ["iron-ore@(10,0)", "iron-ore@(60,30)"]
    assert decide.candidates(t, "iron-ore", min_tiles=5)[0].bounds[0].x == 2


def test_questions_carry_facts_and_defaults(gd):
    t = two_iron_patches()
    q = decide.questions(gd, t, {"iron-ore": 2.0, "copper-ore": 1.0}, 600, product="x")
    iron = q["patches"]["iron-ore"]
    assert iron["default"] == "iron-ore@(10,0)" and iron["demand"] == 2.0
    ids = [c["id"] for c in iron["candidates"]]
    assert ids == ["iron-ore@(10,0)", "iron-ore@(60,30)"]
    rich = iron["candidates"][1]
    assert rich["total_amount"] == 20 * 20 * 5000 and rich["tiles"] == 400
    assert rich["distance_to_spawn"] > iron["candidates"][0]["distance_to_spawn"]
    m = rich["mining_at_demand"]
    assert m["drills"] >= 1 and m["belt_rate"] <= 2.0 + 1e-9 and m["first_dry_s_lower_bound"] >= 600
    assert rich["distance_to_other_candidates"]["copper-ore"][0][0] == "copper-ore@(10,40)"
    assert set(q["place_costs"]["default"]) == set(decide.cost_docs())
    assert q["order_priority"]["default"] == list(ORDER_KEYS)
    # no coordinate is asked for: the answer names ids and weights only
    json.dumps(q)


def test_decisions_load_validate_and_choose(tmp_path):
    t = two_iron_patches()
    f = tmp_path / "rich.json"
    f.write_text(
        json.dumps(
            {
                "name": "rich",
                "patches": {"iron-ore": "iron-ore@(60,30)"},
                "place_costs": {"resource": 5.0},
                "order_priority": ["linked_rate", "unit", "placed_neighbours"],
                "answered_by": {"model": "test"},
                "rationale": "more ore",
            }
        )
    )
    d = decide.Decisions.load(f)
    assert d.name == "rich" and d.place_costs == PlaceCosts(resource=5.0)
    assert d.order_priority == ("linked_rate", "unit", "placed_neighbours")
    assert decide.patch_id(d.choose(t, "iron-ore")) == "iron-ore@(60,30)"
    assert decide.patch_id(d.choose(t, "copper-ore")) == "copper-ore@(10,40)"  # default
    assert decide.DEFAULT.choose(t, "iron-ore").bounds[0].x == 10  # nearest
    with pytest.raises(ValueError):
        decide.Decisions(patches={"iron-ore": "iron-ore@(1,1)"}).choose(t, "iron-ore")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"place_costs": {"speed": 1}}))
    with pytest.raises(ValueError):
        decide.Decisions.load(bad)
    bad.write_text(json.dumps({"order_priority": ["unit"]}))
    with pytest.raises(ValueError):
        decide.Decisions.load(bad)
    bad.write_text(json.dumps({"patches": {"iron-ore": [60, 30]}}))
    with pytest.raises(ValueError):
        decide.Decisions.load(bad)
    assert d.to_json()["patches"] == {"iron-ore": "iron-ore@(60,30)"}
