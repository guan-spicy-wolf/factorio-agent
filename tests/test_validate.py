import math

from fgen.data.schema import GameData
from fgen.geom import footprint, inserter, pole
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout.model import Interface, Layout, Placement
from fgen.layout.validate import validate

N, E, S, W = Direction.N, Direction.E, Direction.S, Direction.W


def at(gd: GameData, name: str, x: int, y: int, d: Direction = N, **extra: object) -> Placement:
    return Placement(
        name, footprint.center_for_tile_anchor(gd, name, Tile(x, y), d), d, dict(extra)
    )


def codes(vs: list) -> list[str]:
    return sorted(v.code for v in vs)


def powered(gd: GameData, ps: list[Placement]) -> list[Placement]:
    """Add one small pole next to the origin so electric inserters are covered."""
    return [*ps, at(gd, "small-electric-pole", 1, 1)]


# ---- overlap -----------------------------------------------------------------


def test_overlap(gd: GameData) -> None:
    w, _ = footprint.size(gd, "stone-furnace")
    bad = Layout([at(gd, "stone-furnace", 0, 0), at(gd, "stone-furnace", w - 1, 0)])
    vs = validate(bad, gd)
    assert codes(vs) == ["overlap"]
    assert len(vs[0].tiles) == footprint.size(gd, "stone-furnace")[1]
    good = Layout([at(gd, "stone-furnace", 0, 0), at(gd, "stone-furnace", w, 0)])
    assert validate(good, gd) == []


# ---- inserters ---------------------------------------------------------------


def _inserter_case(gd: GameData, name: str = "inserter") -> tuple[Placement, Tile, Tile]:
    ins = at(gd, name, 0, 0, N)
    pu = inserter.pickup_tile(gd, name, ins.position, N)
    dr = inserter.drop_tile(gd, name, ins.position, N)
    return ins, pu, dr


def test_inserter_pickup_and_drop(gd: GameData) -> None:
    for name in ["inserter", "long-handed-inserter"]:
        ins, pu, dr = _inserter_case(gd, name)
        src = at(gd, "wooden-chest", pu.x, pu.y)
        dst = at(gd, "wooden-chest", dr.x, dr.y)
        assert validate(Layout(powered(gd, [ins, src, dst])), gd) == []
        assert codes(validate(Layout(powered(gd, [ins, dst])), gd)) == ["inserter.pickup_empty"]
        assert codes(validate(Layout(powered(gd, [ins, src])), gd)) == ["inserter.drop_empty"]


def test_inserter_interface_tile_counts_as_target(gd: GameData) -> None:
    ins, pu, dr = _inserter_case(gd)
    dst = at(gd, "wooden-chest", dr.x, dr.y)
    iface = Interface("item", "iron-ore", pu, E, "left", 1.0, "input")
    assert validate(Layout(powered(gd, [ins, dst])), gd, [iface]) == []


def test_inserter_needs_item_holder_not_any_entity(gd: GameData) -> None:
    ins, pu, dr = _inserter_case(gd)
    pole_on_pickup = at(gd, "small-electric-pole", pu.x, pu.y)
    dst = at(gd, "wooden-chest", dr.x, dr.y)
    assert codes(validate(Layout([ins, pole_on_pickup, dst]), gd)) == ["inserter.pickup_empty"]


def test_inserter_rotated(gd: GameData) -> None:
    ins = at(gd, "inserter", 0, 0, E)
    pu = inserter.pickup_tile(gd, "inserter", ins.position, E)
    dr = inserter.drop_tile(gd, "inserter", ins.position, E)
    assert pu.x > 0 > dr.x  # E-facing picks from the east, drops west
    lay = Layout([ins, at(gd, "iron-chest", pu.x, pu.y), at(gd, "iron-chest", dr.x, dr.y)])
    assert validate(Layout(powered(gd, lay.placements)), gd) == []


# ---- power -------------------------------------------------------------------


def test_power_uncovered(gd: GameData) -> None:
    ins, pu, dr = _inserter_case(gd)
    chests = [at(gd, "wooden-chest", pu.x, pu.y), at(gd, "wooden-chest", dr.x, dr.y)]
    x0, *_ = pole.supply_range(gd, "small-electric-pole", Tile(0, 0).center())
    far = -x0 + 1  # one tile beyond the supply reach, derived from the seed
    lay = Layout([ins, *chests, at(gd, "small-electric-pole", far, 0)])
    vs = validate(lay, gd)
    assert codes(vs) == ["power.uncovered"]
    near = Layout([ins, *chests, at(gd, "small-electric-pole", far - 1, 0)])
    assert validate(near, gd) == []
    # a burner inserter needs no pole
    b = at(gd, "burner-inserter", 0, 0, N)
    assert validate(Layout([b, *chests]), gd) == []


def test_power_uncovered_without_any_pole(gd: GameData) -> None:
    ins, pu, dr = _inserter_case(gd)
    chests = [at(gd, "wooden-chest", pu.x, pu.y), at(gd, "wooden-chest", dr.x, dr.y)]
    assert codes(validate(Layout([ins, *chests]), gd)) == ["power.uncovered"]


def test_power_disconnected(gd: GameData) -> None:
    name = "small-electric-pole"
    wire = gd.entity(name).maximum_wire_distance
    reach = math.floor(wire)
    a = at(gd, name, 0, 0)
    assert pole.wire_reaches(gd, name, a.position, name, at(gd, name, reach, 0).position)
    assert validate(Layout([a, at(gd, name, reach, 0)]), gd) == []
    vs = validate(Layout([a, at(gd, name, reach + 1, 0)]), gd)
    assert codes(vs) == ["power.disconnected"]
    # a third pole in between joins the network again
    assert validate(Layout([a, at(gd, name, reach, 0), at(gd, name, reach + 1, 0)]), gd) == []


# ---- belts -------------------------------------------------------------------


def _belt_row(gd: GameData, n: int, d: Direction = E) -> list[Placement]:
    return [at(gd, "transport-belt", x, 0, d) for x in range(n)]


def test_belt_discontinuous(gd: GameData) -> None:
    row = _belt_row(gd, 3)
    vs = validate(Layout(row), gd)
    assert codes(vs) == ["belt.discontinuous"]
    assert Tile(2, 0) in vs[0].tiles
    out = Interface("item", "iron-plate", Tile(2, 0), E, "right", 7.5, "output")
    assert validate(Layout(row), gd, [out]) == []
    assert validate(Layout(row), gd, belt_terminals=[Tile(2, 0)]) == []
    # an *input* interface on the last tile does not excuse the dead end
    inp = Interface("item", "iron-ore", Tile(2, 0), E, "left", 7.5, "input")
    assert codes(validate(Layout(row), gd, [inp])) == ["belt.discontinuous"]


def test_belt_head_on_and_turns(gd: GameData) -> None:
    head_on = [at(gd, "transport-belt", 0, 0, E), at(gd, "transport-belt", 1, 0, W)]
    assert codes(validate(Layout(head_on), gd)) == ["belt.discontinuous"] * 2
    turn = [at(gd, "transport-belt", 0, 0, E), at(gd, "transport-belt", 1, 0, S)]
    assert codes(validate(Layout(turn), gd, belt_terminals=[Tile(1, 0)])) == []


# ---- undergrounds ------------------------------------------------------------


def test_underground_pairing(gd: GameData) -> None:
    name = "underground-belt"
    reach = gd.entity(name).max_distance
    inp = at(gd, name, 0, 0, E, type="input")
    term = [Tile(reach, 0), Tile(reach + 1, 0)]
    ok = Layout([inp, at(gd, name, reach, 0, E, type="output")])
    assert validate(ok, gd, belt_terminals=term) == []
    too_far = Layout([inp, at(gd, name, reach + 1, 0, E, type="output")])
    assert codes(validate(too_far, gd, belt_terminals=term)) == ["underground.unpaired"] * 2
    wrong_dir = Layout([inp, at(gd, name, 2, 0, W, type="output")])
    assert "underground.unpaired" in codes(validate(wrong_dir, gd, belt_terminals=[Tile(2, 0)]))
    # different tier does not pair
    other = Layout([inp, at(gd, "fast-underground-belt", 2, 0, E, type="output")])
    assert "underground.unpaired" in codes(validate(other, gd, belt_terminals=[Tile(2, 0)]))


def test_belt_into_underground_entrance(gd: GameData) -> None:
    name = "underground-belt"
    lay = Layout(
        [
            at(gd, "transport-belt", 0, 0, E),
            at(gd, name, 1, 0, E, type="input"),
            at(gd, name, 3, 0, E, type="output"),
        ]
    )
    assert validate(lay, gd, belt_terminals=[Tile(3, 0)]) == []


# ---- fluids ------------------------------------------------------------------


def test_fluid_unintended_adjacency(gd: GameData) -> None:
    lay = Layout([at(gd, "pipe", 0, 0), at(gd, "pipe", 1, 0), at(gd, "pipe", 5, 0)])
    vs = validate(lay, gd, fluids={0: "water", 1: "crude-oil", 2: "steam"})
    assert codes(vs) == ["fluid.unintended_adjacency"]
    assert validate(lay, gd, fluids={0: "water", 1: "water", 2: "steam"}) == []
    # without declared fluids nothing can be judged
    assert validate(lay, gd) == []


def test_fluid_adjacency_needs_facing_connections(gd: GameData) -> None:
    # pipe-to-ground facing N has its normal connection to the north only
    lay = Layout([at(gd, "pipe", 0, 0), at(gd, "pipe-to-ground", 1, 0, N)])
    assert validate(lay, gd, fluids={0: "water", 1: "crude-oil"}) == []


# ---- data errors -------------------------------------------------------------


def test_unknown_and_misaligned(gd: GameData) -> None:
    lay = Layout(
        [Placement("no-such-thing", Vec2(0.5, 0.5)), Placement("stone-furnace", Vec2(0.5, 0.5))]
    )
    assert codes(validate(lay, gd)) == ["entity.unknown", "position.misaligned"]
