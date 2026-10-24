"""M2 (phase 2): drills over an ore patch, from hand-written terrain (no server)."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace

import pytest

from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.gen.mining_patch import MiningPatch, MiningPatchParams, generate, render
from fgen.geom import belt as beltgeom
from fgen.geom import footprint, mining
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Layout, Placement
from fgen.layout.validate import validate
from fgen.terrain import paint
from fgen.terrain.model import Patch, Terrain, patches

AMOUNT = 1000


def _patch(shape, resource="iron-ore", **kw) -> tuple[Patch, Terrain]:
    t = Terrain.from_resources(paint.to_resources(shape, resource), **kw)
    (p,) = patches(t, resource)
    return p, t


SHAPES = {
    "rect": paint.rectangle(0, 0, 20, 14, AMOUNT),
    "l": paint.l_shape(0, 0, 24, 20, 8, AMOUNT),
    "hole": paint.with_hole(paint.rectangle(0, 0, 24, 18, AMOUNT), 8, 5, 8, 8),
}


def _drills(unit, gd):
    return [p for p in unit.layout.placements if gd.entity(p.name).type == "mining-drill"]


def _check_unit(unit, gd: GameData, p: MiningPatchParams, patch: Patch, terrain: Terrain) -> None:
    assert MiningPatch().invariants(unit, gd, terrain) == []
    rate = mining.drill_rate(gd, p.drill, p.resource)
    drills = _drills(unit, gd)
    assert drills and all(d.name == p.drill for d in drills)
    assert unit.meta["anchor"]["drills"] == len(drills)
    outs = [i for i in unit.interfaces if i.kind == "item"]
    assert outs and all(i.role == "output" and i.material == p.resource for i in outs)
    assert sum(i.rate for i in outs) == pytest.approx(len(drills) * rate)
    cap = tp.belt_lane_capacity(gd, p.belt)
    for b in unit.meta["belts"]:
        for n in b["lanes"].values():
            assert n * rate <= cap + 1e-9
    # each drill searches at least min_ore_tiles_per_drill patch tiles
    for d in drills:
        ore = mining.search_tiles(gd, d.name, d.position) & patch.tiles
        assert len(ore) >= p.min_ore_tiles_per_drill
    a = unit.meta["anchor"]
    assert a["resource"] == p.resource and a["total_amount"] == patch.total_amount
    assert a["est_depletion_s"] == pytest.approx(patch.total_amount / (len(drills) * rate))
    assert [i for i in unit.interfaces if i.kind == "power"]


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("axis", ["x", "y"])
def test_shapes_validate(gd, shape, axis):
    patch, terrain = _patch(SHAPES[shape])
    p = MiningPatchParams(axis=axis)
    unit = generate(p, gd, patch, terrain)
    _check_unit(unit, gd, p, patch, terrain)
    flow = Direction.E if axis == "x" else Direction.S
    outs = [i for i in unit.interfaces if i.kind == "item"]
    assert {i.direction for i in outs} == {flow}
    # outputs sit at the belt's downstream end: nothing of the unit beyond them
    tiles = unit.layout.tiles(gd)
    for i in outs:
        assert i.tile in tiles and i.tile.neighbor(flow) not in tiles


@pytest.mark.parametrize("flow", ["+", "-"])
def test_flow_direction(gd, flow):
    patch, terrain = _patch(SHAPES["rect"])
    for axis, d in (("x", Direction.E), ("y", Direction.S)):
        p = MiningPatchParams(axis=axis, flow=flow)
        unit = generate(p, gd, patch, terrain)
        _check_unit(unit, gd, p, patch, terrain)
        want = d if flow == "+" else d.opposite
        assert {i.direction for i in unit.interfaces if i.kind == "item"} == {want}


def test_drills_face_belts_and_fill_both_lanes(gd):
    patch, terrain = _patch(SHAPES["rect"])
    unit = generate(MiningPatchParams(), gd, patch, terrain)
    at = unit.layout.tile_index(gd)
    lanes = Counter()
    for d in _drills(unit, gd):
        t = mining.drop_tile(gd, d.name, d.position, d.direction)
        (belt,) = [unit.layout.placements[i] for i in at[t]]
        assert gd.entity(belt.name).type == "transport-belt"
        lanes[mining.drop_lane(gd, d.name, d.position, d.direction, belt.direction)] += 1
    assert set(lanes) == {"left", "right"}


@pytest.mark.parametrize(
    "change",
    [
        {"pole": "medium-electric-pole"},
        {"pole": "substation"},
        {"belt": "fast-transport-belt"},
        {"drill": "big-mining-drill", "pole": "substation"},
        {"min_ore_tiles_per_drill": 6},
        {"resource": "copper-ore"},
    ],
    ids=lambda c: ",".join(f"{k}={v}" for k, v in c.items()),
)
def test_swap_entities_by_params_only(gd, change):
    resource = change.get("resource", "iron-ore")
    patch, terrain = _patch(paint.rectangle(0, 0, 30, 30, AMOUNT), resource)
    p = replace(MiningPatchParams(), **change)
    unit = generate(p, gd, patch, terrain)
    _check_unit(unit, gd, p, patch, terrain)
    names = {pl.name for pl in unit.layout.placements}
    assert names == {p.drill, p.belt, p.pole}


def test_pole_rows_derived_from_supply_area(gd):
    """One pole row per belt when the pole's supply reaches both drill rows (small and
    medium poles with the electric drill); poles never where a drill is."""
    patch, terrain = _patch(paint.rectangle(0, 0, 30, 30, AMOUNT))
    for pole_name in ("small-electric-pole", "medium-electric-pole"):
        unit = generate(MiningPatchParams(pole=pole_name), gd, patch, terrain)
        poles = [q for q in unit.layout.placements if q.name == pole_name]
        belt_rows = {i.tile.y for i in unit.interfaces if i.kind == "item"}
        pole_rows = {q.position.floor().y for q in poles}
        assert len(pole_rows) <= len(belt_rows) + 1
    small = generate(MiningPatchParams(), gd, patch, terrain)
    medium = generate(MiningPatchParams(pole="medium-electric-pole"), gd, patch, terrain)
    # the medium pole's wider supply/longer wire costs fewer drill slots
    assert (
        medium.meta["dropped_by_reason"]["pole_slot"] < small.meta["dropped_by_reason"]["pole_slot"]
    )


def test_lane_capacity_truncation(gd):
    patch, terrain = _patch(paint.rectangle(0, 0, 90, 3, AMOUNT))
    p = MiningPatchParams(pole="medium-electric-pole")
    unit = generate(p, gd, patch, terrain)
    _check_unit(unit, gd, p, patch, terrain)
    rate = mining.drill_rate(gd, p.drill, p.resource)
    per_lane = math.floor(tp.belt_lane_capacity(gd, p.belt) / rate + 1e-9)
    cut = [d for d in unit.meta["dropped_drills"] if d["reason"] == "lane_capacity"]
    assert cut
    assert max(n for b in unit.meta["belts"] for n in b["lanes"].values()) == per_lane
    # cut drills are upstream (west) of every kept drill on their belt
    kept_x = min(d.position.x for d in _drills(unit, gd))
    assert all(d["position"][0] < kept_x + 3 * 30 for d in cut)
    assert unit.meta["suggestions"]
    changes = unit.meta["suggestion_changes"]
    belt_changes = [c for c in changes if "belt" in c]
    assert belt_changes
    faster = generate(replace(p, **belt_changes[-1]), gd, patch, terrain)
    assert "lane_capacity" not in faster.meta["dropped_by_reason"]
    assert faster.meta["anchor"]["drills"] > unit.meta["anchor"]["drills"]
    assert beltgeom.items_per_second(gd, faster.meta["params"]["belt"]) > beltgeom.items_per_second(
        gd, p.belt
    )


def test_max_belts(gd):
    patch, terrain = _patch(paint.rectangle(0, 0, 20, 30, AMOUNT))
    full = generate(MiningPatchParams(), gd, patch, terrain)
    one = generate(MiningPatchParams(max_belts=1), gd, patch, terrain)
    assert len([i for i in full.interfaces if i.kind == "item"]) > 1
    assert len([i for i in one.interfaces if i.kind == "item"]) == 1
    assert one.meta["dropped_by_reason"].get("max_belts")


def test_obstacles_respected(gd):
    shape = paint.rectangle(0, 0, 24, 14, AMOUNT)
    water = {Tile(x, y) for x in range(10, 14) for y in range(-6, 20)}
    rocks = {Tile(3, 3), Tile(18, 9)}
    patch, terrain = _patch(shape, water=water, occupied=rocks)
    p = MiningPatchParams()
    unit = generate(p, gd, patch, terrain)
    _check_unit(unit, gd, p, patch, terrain)  # includes terrain.unbuildable
    tiles = unit.layout.tiles(gd)
    assert not tiles & water and not tiles & rocks
    reasons = unit.meta["dropped_by_reason"]
    assert reasons.get("belt_blocked")  # drills west of the water lose their belt


def test_render_shows_ore_and_drills(gd):
    patch, terrain = _patch(SHAPES["hole"])
    unit = generate(MiningPatchParams(), gd, patch, terrain)
    art = render(unit, gd, terrain)
    assert "D" in art and ">" in art and "P" in art and ":" in art and "." in art


def test_infeasible_cases(gd):
    patch, terrain = _patch(paint.rectangle(0, 0, 10, 10, AMOUNT))
    with pytest.raises(Infeasible) as e:
        generate(MiningPatchParams(drill="assembling-machine-1"), gd, patch, terrain)
    assert any("electric-mining-drill" in s for s in e.value.suggestions)
    with pytest.raises(Infeasible) as e:
        generate(MiningPatchParams(drill="burner-mining-drill"), gd, patch, terrain)
    assert "drill='electric-mining-drill'" in e.value.suggestions
    with pytest.raises(Infeasible):
        generate(MiningPatchParams(resource="crude-oil"), gd, patch, terrain)
    with pytest.raises(Infeasible):
        generate(MiningPatchParams(resource="uranium-ore"), gd, patch, terrain)
    with pytest.raises(Infeasible) as e:
        generate(MiningPatchParams(resource="coal"), gd, patch, terrain)
    assert e.value.suggestions == ["resource='iron-ore'"]
    # everything around the ore is occupied: nothing fits
    blocked = Terrain(
        buildable=terrain.buildable,
        resources=terrain.resources,
        occupied=frozenset(terrain.buildable),
    )
    with pytest.raises(Infeasible) as e:
        generate(MiningPatchParams(), gd, patch, blocked)
    assert "no electric-mining-drill fits" in e.value.reason
    # a single ore tile but drills need 3 tiles in their area: suggest relaxing it
    one, t1 = _patch(paint.rectangle(0, 0, 1, 2, AMOUNT))
    with pytest.raises(Infeasible) as e:
        generate(MiningPatchParams(min_ore_tiles_per_drill=3), gd, one, t1)
    assert "min_ore_tiles_per_drill=1" in e.value.suggestions
    assert generate(MiningPatchParams(min_ore_tiles_per_drill=1), gd, one, t1)


def test_drill_invariants(gd):
    d = "electric-mining-drill"
    c = footprint.center_for_tile_anchor(gd, d, Tile(0, 0))
    drop = mining.drop_tile(gd, d, c, Direction.N)
    lone = Layout([Placement(d, c, Direction.N)])
    codes = {v.code for v in validate(lone, gd, resources={})}
    assert {"drill.output_blocked", "drill.no_ore"} <= codes
    belt = Placement("transport-belt", drop.center(), Direction.E)
    ok = Layout([Placement(d, c, Direction.N), belt])
    far = Tile(10, 10)
    near = Tile(drop.x + 2, drop.y + 3)  # inside the 5x5 search area, outside the footprint
    assert near in mining.search_tiles(gd, d, c)
    codes = {v.code for v in validate(ok, gd, resources={near: ("iron-ore", 5)})}
    assert not {"drill.output_blocked", "drill.no_ore"} & codes
    codes = {v.code for v in validate(ok, gd, resources={far: ("iron-ore", 5)})}
    assert "drill.no_ore" in codes
    # oil under an electric drill does not count
    codes = {v.code for v in validate(ok, gd, resources={near: ("crude-oil", 5)})}
    assert "drill.no_ore" in codes
    # without resources the ore check is skipped
    assert "drill.no_ore" not in {v.code for v in validate(ok, gd)}


def test_mining_sweep_writes_contracts(gd, tmp_path):
    from fgen.gen import mining_sweep

    jsonl, md = mining_sweep.write(gd, tmp_path)
    import json

    rows = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert [r["case"] for r in rows] == [c.name for c in mining_sweep.CASES]
    for r in rows:
        assert r["measured"] is None
        assert r["contract"]["total"] == pytest.approx(
            r["contract"]["drills"]
            * mining.drill_rate(gd, r["params"]["drill"], r["params"]["resource"])
        )
    text = md.read_text()
    assert "## Summary" in text and "## l-shape" in text and ":::" in text
    # a measurement survives a re-run while the blueprint is unchanged
    rows[0]["measured"] = {"sink_throughput": [1.0], "deviation": [0.0]}
    jsonl.write_text("".join(json.dumps(r) + "\n" for r in rows))
    mining_sweep.write(gd, tmp_path)
    again = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert again[0]["measured"] == rows[0]["measured"]


def test_cli_gen_mining(capsys):
    from fgen.cli import main

    assert main(["gen", "mining", "--case", "l-shape", "--pole", "medium-electric-pole"]) == 0
    out = capsys.readouterr().out
    assert "D" in out and ":" in out and "output item" in out
    assert main(["gen", "mining", "--case", "rect-1belt", "--drill", "pumpjack"]) == 2


def test_low_ore_and_mixed_resources(gd):
    # rich core, poor fringe column on the west, stone touching the east edge
    shape = paint.rectangle(0, 0, 18, 10, AMOUNT)
    shape.update(paint.rectangle(-3, 0, 3, 10, 2))
    res = paint.to_resources(shape, "iron-ore")
    res.update(paint.to_resources(paint.rectangle(18, 0, 6, 10, AMOUNT), "stone"))
    terrain = Terrain.from_resources(res)
    patch = max(patches(terrain, "iron-ore"), key=lambda q: len(q.tiles))
    unit = generate(MiningPatchParams(), gd, patch, terrain)
    for d in _drills(unit, gd):
        area = mining.search_tiles(gd, d.name, d.position)
        assert not any(terrain.resources.get(t, ("",))[0] == "stone" for t in area)
    assert unit.meta["dropped_by_reason"].get("mixed_resource")
    mixed = generate(MiningPatchParams(allow_mixed=True), gd, patch, terrain)
    assert mixed.meta["anchor"]["drills"] > unit.meta["anchor"]["drills"]
    need = 100
    rich = generate(MiningPatchParams(min_ore_amount_per_drill=need), gd, patch, terrain)
    assert rich.meta["dropped_by_reason"].get("low_ore")
    for d in _drills(rich, gd):
        area = mining.search_tiles(gd, d.name, d.position)
        assert sum(patch.amounts.get(t, 0) for t in area) >= need
    a = rich.meta["anchor"]
    assert (
        a["first_dry_s_lower_bound"]
        >= need / mining.drill_rate(gd, "electric-mining-drill", "iron-ore") / 4
    )
