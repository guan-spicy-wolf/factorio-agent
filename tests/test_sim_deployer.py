"""M2: blueprint deployment round trip (the phase-0 completion criterion).

Every layout is exported with ``unit_to_blueprint``/``layout_to_blueprint``, imported in
the game, built as ghosts, revived, read back and compared with the original.
"""

from __future__ import annotations

import pytest

from fgen.gen.smelting_row import SmeltingRowParams, generate
from fgen.geom import footprint
from fgen.geom import inserter as insgeom
from fgen.geom.vec import CARDINALS, Direction, Tile, Vec2
from fgen.layout import transform as tf
from fgen.layout.compare import describe_diff, same
from fgen.layout.model import Layout, Placement
from fgen.sim.deployer import RconDeployer

pytestmark = pytest.mark.headless

FIELD = (Tile(-100, -100), Tile(99, 99))
E, N, S, W = Direction.E, Direction.N, Direction.S, Direction.W


def P(name, x, y, d=N, **extra):
    return Placement(name, Vec2(x, y), d, extra)


def undergrounds() -> Layout:
    return Layout(
        [
            # east, yellow
            P("transport-belt", 0.5, 0.5, E),
            P("underground-belt", 1.5, 0.5, E, type="input"),
            P("underground-belt", 4.5, 0.5, E, type="output"),
            P("transport-belt", 5.5, 0.5, E),
            # west, red
            P("transport-belt", 6.5, 2.5, W),
            P("fast-underground-belt", 5.5, 2.5, W, type="input"),
            P("fast-underground-belt", 1.5, 2.5, W, type="output"),
            P("transport-belt", 0.5, 2.5, W),
            # north, yellow
            P("transport-belt", 8.5, 6.5, N),
            P("underground-belt", 8.5, 5.5, N, type="input"),
            P("underground-belt", 8.5, 1.5, N, type="output"),
            P("transport-belt", 8.5, 0.5, N),
            # south, blue, longest gap the yellow one could not do
            P("express-transport-belt", 10.5, 0.5, S),
            P("express-underground-belt", 10.5, 1.5, S, type="input"),
            P("express-underground-belt", 10.5, 8.5, S, type="output"),
            P("express-transport-belt", 10.5, 9.5, S),
        ]
    )


def inserters(gd) -> Layout:
    out = []
    for k, d in enumerate(CARDINALS):
        for row, name in enumerate(("inserter", "long-handed-inserter", "fast-inserter")):
            c = Tile(2 + 6 * k, 2 + 6 * row).center()
            out.append(Placement(name, c, d))
            for t in (insgeom.pickup_tile(gd, name, c, d), insgeom.drop_tile(gd, name, c, d)):
                out.append(P("wooden-chest", t.x + 0.5, t.y + 0.5))
    out.append(P("small-electric-pole", 4.5, 4.5))
    out.append(P("medium-electric-pole", 10.5, 10.5))
    return Layout(out)


def machines_and_poles(gd) -> Layout:
    return Layout(
        [
            P("small-electric-pole", 0.5, 0.5),
            P("medium-electric-pole", 5.5, 0.5),
            P("big-electric-pole", 10, 1),
            P("substation", 16, 1),
            P("assembling-machine-1", 2.5, 4.5, recipe="iron-gear-wheel"),
            P("assembling-machine-2", 6.5, 4.5, E, recipe="copper-cable"),
            P("assembling-machine-3", 10.5, 4.5),
            P("pipe", 13.5, 3.5),
            P("pipe", 14.5, 3.5),
            P("pipe-to-ground", 15.5, 3.5, W),
            P("pipe-to-ground", 19.5, 3.5, E),
            P("splitter", 13.5, 6, E),
            P("transport-belt", 12.5, 5.5, E),
            P("transport-belt", 12.5, 6.5, E),
            P("stone-furnace", 1, 8),
            P("steel-furnace", 4, 8),
            P("electric-furnace", 7.5, 8.5),
            P("iron-chest", 10.5, 8.5),
            P("steel-chest", 11.5, 8.5),
        ]
    )


@pytest.fixture(scope="module")
def dep(server, gd):
    d = RconDeployer(server.rcon, gd)
    d.clear(FIELD)
    return d


def _roundtrip(dep, gd, layout, origin, direct=False):
    dep.clear(FIELD)
    (dep.place_direct if direct else dep.place_layout)(layout, origin)
    back = dep.read_back_layout(layout, origin)
    assert same(layout, back, gd), describe_diff(layout, back, gd)
    return back


ORIGINS = [Tile(-60, -40), Tile(13, 7)]


@pytest.mark.parametrize("origin", ORIGINS, ids=str)
def test_smelting_row_roundtrip(dep, gd, origin):
    unit = generate(SmeltingRowParams(), gd)
    dep.clear(FIELD)
    rep = dep.place(unit, origin)
    assert rep.revived == len(unit.layout.placements)
    back = dep.read_back_layout(unit.layout, origin)
    assert same(unit.layout, back, gd), describe_diff(unit.layout, back, gd)


@pytest.mark.parametrize(
    "params",
    [
        SmeltingRowParams(target_rate=15, belt="fast-transport-belt"),
        SmeltingRowParams(furnace="steel-furnace", pole="medium-electric-pole"),
        SmeltingRowParams(target_rate=5, furnace="electric-furnace", sides=2),
    ],
    ids=["red-15", "steel-medium", "electric-2side"],
)
def test_smelting_row_variants_roundtrip(dep, gd, params):
    _roundtrip(dep, gd, generate(params, gd).layout, Tile(-80, -30))


@pytest.mark.parametrize("d", [E, S, W], ids=lambda d: d.name)
def test_rotated_smelting_row_roundtrip(dep, gd, d):
    """Rotated layouts write non-N directions on furnaces/poles; the game ignores them."""
    layout = tf.normalize(tf.rotate(generate(SmeltingRowParams(), gd).layout, d), gd)
    _roundtrip(dep, gd, layout, Tile(-70, -70))


@pytest.mark.parametrize("build", [undergrounds, inserters, machines_and_poles])
@pytest.mark.parametrize("direct", [False, True], ids=["blueprint", "direct"])
def test_handwritten_roundtrip(dep, gd, build, direct):
    layout = build() if build is undergrounds else build(gd)
    _roundtrip(dep, gd, layout, Tile(-20, -20), direct=direct)


def test_handwritten_layouts_are_tile_aligned(gd):
    for layout in (undergrounds(), inserters(gd), machines_and_poles(gd)):
        tiles: set[Tile] = set()
        for p in layout.placements:
            ts = footprint.tiles_of(gd, p.name, p.position, p.direction)
            assert not ts & tiles, p
            tiles |= ts


def test_read_back_excludes_and_offsets(dep, gd):
    layout = undergrounds()
    dep.clear(FIELD)
    rep = dep.place_layout(layout, Tile(5, 5))
    back = dep.read_back_layout(layout, Tile(5, 5), exclude=rep.unit_numbers[:2])
    assert len(back.placements) == len(layout.placements) - 2
    shifted = dep.read_back_layout(layout, Tile(4, 5))
    assert not same(layout, shifted, gd)
