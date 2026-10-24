"""Layout comparison used by the deployer round trip (no server)."""

from __future__ import annotations

from fgen.geom.footprint import direction_matters
from fgen.geom.vec import Direction, Tile, Vec2
from fgen.layout.compare import describe_diff, diff, same
from fgen.layout.model import Layout, Placement
from fgen.sim.deployer import _as_list, entity_to_placement


def P(name, x, y, d=Direction.N, **extra):
    return Placement(name, Vec2(x, y), d, extra)


def test_order_and_float_noise_ignored(gd):
    a = Layout([P("transport-belt", 0.5, 0.5, Direction.E), P("inserter", 1.5, 0.5)])
    b = Layout([P("inserter", 1.5000001, 0.4999999), P("transport-belt", 0.5, 0.5, Direction.E)])
    assert same(a, b, gd)
    assert a.same_as(b, gd)
    assert a.normalized(gd) == b.normalized(gd)


def test_direction_ignored_only_for_directionless(gd):
    furn = Layout([P("stone-furnace", 1, 1, Direction.E)])
    assert same(furn, Layout([P("stone-furnace", 1, 1)]), gd)
    belt = Layout([P("transport-belt", 0.5, 0.5, Direction.E)])
    assert not same(belt, Layout([P("transport-belt", 0.5, 0.5)]), gd)
    assert not direction_matters(gd, "small-electric-pole")
    assert not direction_matters(gd, "iron-chest")
    assert direction_matters(gd, "inserter")
    assert direction_matters(gd, "pipe-to-ground")
    # item-only recipe: the game reports N
    assert not direction_matters(gd, "assembling-machine-2", "copper-cable")


def test_underground_type_and_recipe_compared(gd):
    a = Layout([P("underground-belt", 0.5, 0.5, Direction.E)])  # defaults to input
    assert same(a, Layout([P("underground-belt", 0.5, 0.5, Direction.E, type="input")]), gd)
    assert not same(a, Layout([P("underground-belt", 0.5, 0.5, Direction.E, type="output")]), gd)
    m = Layout([P("assembling-machine-1", 1.5, 1.5, recipe="iron-gear-wheel")])
    assert not same(m, Layout([P("assembling-machine-1", 1.5, 1.5)]), gd)
    # unrelated extras are not compared
    assert same(
        m, Layout([P("assembling-machine-1", 1.5, 1.5, recipe="iron-gear-wheel", tag=1)]), gd
    )


def test_multiset_diff(gd):
    a = Layout([P("inserter", 0.5, 0.5), P("inserter", 0.5, 0.5)])
    b = Layout([P("inserter", 0.5, 0.5)])
    only_a, only_b = diff(a, b, gd)
    assert len(only_a) == 1 and not only_b
    assert describe_diff(a, a, gd) == "(equal)"


def test_entity_to_placement():
    e = {
        "name": "underground-belt",
        "position": {"x": 12.5, "y": -3.5},
        "direction": 4,
        "belt_to_ground_type": "output",
    }
    p = entity_to_placement(e, Tile(10, -5))
    assert p.position == Vec2(2.5, 1.5)
    assert p.direction == Direction.E
    assert p.extra == {"type": "output"}
    assert _as_list({}) == []
    assert _as_list({"2": "b", "1": "a"}) == ["a", "b"]
