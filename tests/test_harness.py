"""Harness planning and sweep bookkeeping (no server)."""

from __future__ import annotations

import json

import pytest

from fgen.gen import sweep
from fgen.gen.smelting_row import SmeltingRowParams, generate
from fgen.geom import pole
from fgen.geom.vec import Tile, Vec2
from fgen.sim import harness as hs

PARAMS = [
    SmeltingRowParams(),
    SmeltingRowParams(target_rate=15, belt="fast-transport-belt"),
    SmeltingRowParams(furnace="steel-furnace", pole="medium-electric-pole"),
    SmeltingRowParams(target_rate=15, furnace="electric-furnace", sides=2, fuel_lane=False),
]


@pytest.mark.parametrize("p", PARAMS, ids=lambda p: f"{p.furnace}-{p.target_rate:g}-{p.sides}")
def test_harness_plan_avoids_unit_and_covers_interfaces(gd, p):
    unit = generate(p, gd)
    origin = Tile(-60, -20)
    h = hs.plan(unit, gd, origin)
    unit_tiles = {t + origin for t in unit.layout.tiles(gd)}
    assert not h.tiles & unit_tiles
    # one source per material input (fuel lane of an electric furnace is unused)
    want = sorted(
        str(i.material)
        for i in unit.interfaces
        if i.kind == "item" and i.role == "input" and i.material
    )
    assert sorted(item for _, item in h.sources) == want
    outs = [i for i in unit.interfaces if i.kind == "item" and i.role == "output"]
    assert len(h.sinks) == len(outs)
    # harness pole in wire reach of the unit's power-interface pole
    pw = next(i for i in unit.interfaces if i.kind == "power")
    up = next(q for q in unit.layout.placements if q.position.floor() == pw.tile)
    spec = h.specs[h.power_pole]
    assert spec["name"] == up.name
    a = Vec2(*spec["position"])
    b = up.position + Vec2(origin.x, origin.y)
    assert pole.wire_reaches(gd, up.name, a, up.name, b)
    # loaders match the belt tier
    belts = {s["name"] for s in h.specs if gd.entity(s["name"]).type == "transport-belt"}
    loaders = {s["name"] for s in h.specs if gd.entity(s["name"]).type == "loader"}
    assert loaders == {hs.LOADER_FOR_BELT[b] for b in belts}


def test_lane_inputs_are_side_loaded_from_the_lane_side(gd):
    unit = generate(SmeltingRowParams(), gd)
    h = hs.plan(unit, gd, Tile(0, 0))
    ore = next(i for i in unit.interfaces if i.material == "iron-ore")
    # side-load feeders: belts flowing towards the main row, north (left) and south (right)
    feeders = [s for s in h.specs if s["name"] == "transport-belt" and s["direction"] in (0, 8)]
    ys = sorted(s["position"][1] - (ore.tile.y + 0.5) for s in feeders)
    assert ys == [-1, 1]
    north = next(s for s in feeders if s["position"][1] < ore.tile.y)
    assert north["direction"] == 8  # flows south, into the left (north) lane


def _fake_measure(unit):
    rate = unit.meta["target_rate"]
    return {
        "throughput": rate,
        "samples": [rate] * 3,
        "startup_ticks": 600,
        "sample_s": 10.0,
        "warmup_s": 30.0,
        "ticks_per_second": 60,
        "machines": 1,
        "starved_machines": [],
        "power": {"harness_on_unit_network": True},
        "game_version": "test",
    }


def test_sweep_measure_writes_and_preserves(gd, tmp_path):
    jsonl, md = sweep.write_characterization(gd, tmp_path, _fake_measure)
    rows = [json.loads(x) for x in jsonl.read_text().splitlines()]
    assert all(r["measured"] for r in rows)
    infeasible = [r for r in rows if "infeasible" in r]
    assert infeasible and all(r["measured"]["params_changed"] for r in infeasible)
    text = md.read_text()
    assert "## Predicted vs measured" in text
    # a plain sweep keeps measurements whose blueprint did not change
    sweep.write_characterization(gd, tmp_path)
    again = [json.loads(x) for x in jsonl.read_text().splitlines()]
    assert [r["measured"] for r in again] == [r["measured"] for r in rows]
    # ...and drops one whose blueprint changed
    stale = dict(rows[0])
    stale["measured"] = {**rows[0]["measured"], "blueprint_sha": "x"}
    jsonl.write_text("".join(json.dumps(r) + "\n" for r in [stale, *rows[1:]]))
    sweep.write_characterization(gd, tmp_path)
    assert json.loads(jsonl.read_text().splitlines()[0])["measured"] is None


def test_explain_mentions_downstream_starvation():
    row = {
        "params": {},
        "measured": {
            "starved_machines": [20, 22, 24],
            "machines": 24,
            "power": {"harness_on_unit_network": True},
        },
    }
    assert "downstream" in sweep.explain(row)
