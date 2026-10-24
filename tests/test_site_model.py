"""Contract upgrade (plan-phase3a §3.1) and the site model (no server)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fgen.gen.mining_patch import MiningPatchParams
from fgen.gen.mining_patch import generate as gen_mining
from fgen.gen.mining_sweep import CASES, case_terrain
from fgen.gen.smelting_row import SmeltingRowParams
from fgen.gen.smelting_row import generate as gen_smelting
from fgen.geom.vec import CARDINALS, Direction, Tile
from fgen.layout import transform as tf
from fgen.layout.model import (
    Compressed,
    Interface,
    LaneMaterial,
    Layout,
    MinRate,
    PlacedUnit,
    Pose,
    RateProfile,
    Unit,
)
from fgen.site.model import Site, SiteError, link
from fgen.terrain.model import Terrain, area_tiles

from .strategies import units

poses = st.builds(Pose, st.sampled_from(CARDINALS), st.booleans())
origins = st.builds(Tile, st.integers(-50, 50), st.integers(-50, 50))


@given(units(), poses, origins)
def test_interface_world_equals_placing_the_unit(unit: Unit, pose: Pose, origin: Tile) -> None:
    placed = PlacedUnit("u", unit, origin, pose)
    w = placed.world()
    assert w.interfaces == [i.world(origin, pose) for i in unit.interfaces]
    assert w.interfaces == placed.interfaces_world()
    # the same as composing the phase-0 transforms by hand
    u = tf.mirror_x(unit) if pose.mirror else unit
    u = tf.rotate(u, pose.rotation)
    assert w == tf.translate(u, origin.x, origin.y)


def test_world_moves_lane_requirements_with_mirror() -> None:
    i = Interface(
        "item",
        "iron-ore",
        Tile(0, 0),
        Direction.E,
        "left",
        7.5,
        "input",
        requires=(LaneMaterial("left", "iron-ore"), MinRate(7.5), Compressed()),
    )
    r = i.world(Tile(10, 20), Pose(Direction.S))
    assert r.tile == Tile(9, 19) and r.direction == Direction.W and r.lane == "left"
    assert r.lane_materials() == {"left": "iron-ore"}
    m = i.world(Tile(0, 0), Pose(Direction.N, mirror=True))
    assert m.direction == Direction.W and m.lane == "right"
    assert m.lane_materials() == {"right": "iron-ore"}
    assert m.min_rate() == 7.5
    # mirroring twice gives back the left lane
    assert m.world(Tile(0, 0), Pose(Direction.N, mirror=True)) == i


def test_smelting_row_fills_structured_contract(gd) -> None:
    unit = gen_smelting(SmeltingRowParams(target_rate=7.0), gd)
    ore, fuel, out, power = unit.interfaces
    # the lane requirements of the shared input belt hang on its port group
    assert ore.port_group == fuel.port_group
    assert unit.group(ore.port_group).lane_materials() == {"left": "iron-ore", "right": "coal"}
    assert not ore.lane_materials() and not fuel.lane_materials()
    assert any(isinstance(r, Compressed) for r in ore.requires)
    recipe = gd.recipe("iron-plate")
    per_plate = recipe.ingredients[0][1] / recipe.results[0][1]
    assert ore.min_rate() == pytest.approx(7.0 * per_plate)
    assert ore.rate >= ore.min_rate()  # the furnaces could take more than the target
    assert fuel.min_rate() > 0
    assert out.rate_profile == RateProfile.fixed(out.rate, {out.lane: out.rate})
    assert ore.assumptions and out.assumptions  # human-readable strings kept
    assert power.kind == "power" and not power.requires


def test_electric_smelting_row_has_no_lane_requirement(gd) -> None:
    unit = gen_smelting(
        SmeltingRowParams(furnace="electric-furnace", fuel_lane=False, target_rate=5), gd
    )
    ore = unit.interfaces[0]
    assert ore.lane == "both" and not ore.lane_materials() and ore.min_rate() == 5


def test_mining_outputs_carry_rate_profile(gd) -> None:
    case = next(c for c in CASES if c.name == "rect-2belt")
    patch, terrain = case_terrain(case)
    unit = gen_mining(case.params, gd, patch, terrain)
    outs = [i for i in unit.interfaces if i.role == "output"]
    assert len(outs) == 2
    bound = unit.meta["anchor"]["first_dry_s_lower_bound"]
    for i in outs:
        assert i.rate_profile.initial == i.rate
        assert i.rate_profile.depletion_s_lower_bound >= bound - 1e-9
    assert min(i.rate_profile.depletion_s_lower_bound for i in outs) == pytest.approx(bound)


def test_site_occupancy_and_checks(gd) -> None:
    row = gen_smelting(SmeltingRowParams(), gd)
    terrain = Terrain.from_resources({}, area=(Tile(-20, -20), Tile(80, 40)))
    _, h = row.layout.size(gd)
    a = PlacedUnit("a", row, Tile(0, 0))
    b = PlacedUnit("b", row, Tile(0, h + 2))
    site = Site(terrain, [a, b], [link(("a", 2), ("b", 0))])
    occ = site.occupancy(gd)
    assert len(occ) == 2 * len(row.layout.tiles(gd))
    assert site.check(gd) == []
    assert site.interface(link(("a", 2), ("b", 0)).dst).tile == Tile(0, h + 2)
    assert len(site.merged().placements) == 2 * len(row.layout.placements)
    # overlapping units, wrong roles
    bad = Site(terrain, [a, PlacedUnit("c", row, Tile(1, 0))], [link(("a", 0), ("c", 2))])
    probs = bad.check(gd)
    assert any("overlap" in p for p in probs) and any("want output -> input" in p for p in probs)
    with pytest.raises(SiteError):
        Site(terrain, [a, a])
    blocked = Terrain.from_resources(
        {}, area=(Tile(-20, -20), Tile(80, 40)), blocked=area_tiles((Tile(3, 3), Tile(4, 4)))
    )
    assert any("not buildable" in p for p in Site(blocked, [a]).check(gd))


def test_site_fluids_shift_indices(gd) -> None:
    from fgen.geom.vec import Vec2
    from fgen.layout.model import Placement

    pipe = Unit(Layout([Placement("pipe", Vec2(0.5, 0.5))]), [], {"fluids": {0: "water"}})
    site = Site(
        Terrain.from_resources({}, area=(Tile(0, 0), Tile(9, 9))),
        [PlacedUnit("p", pipe), PlacedUnit("q", pipe, Tile(3, 0))],
    )
    assert site.fluids() == {0: "water", 1: "water"}


def test_mining_params_unchanged_by_contract(gd) -> None:
    # the structured fields must not change the generated layout itself
    case = next(c for c in CASES if c.name == "l-shape")
    patch, terrain = case_terrain(case)
    unit = gen_mining(MiningPatchParams(), gd, patch, terrain)
    assert all(not i.requires for i in unit.interfaces)
