from hypothesis import given

from fgen.data.schema import GameData
from fgen.geom import footprint
from fgen.geom.vec import CARDINALS, Direction, Tile, Vec2
from fgen.layout import transform as tf
from fgen.layout.model import Interface, Layout, Placement, Unit
from fgen.layout.render import EMPTY, OVERLAP, to_ascii

from .strategies import layouts, units

cardinals = __import__("hypothesis").strategies.sampled_from(CARDINALS)


@given(layouts(), cardinals)
def test_rotate_four_times_identity(layout: Layout, d: Direction) -> None:
    r = layout
    for _ in range(4):
        r = tf.rotate(r, d)
    assert r == layout


@given(units())
def test_unit_rotate_and_mirror_involutions(unit: Unit) -> None:
    r = unit
    for _ in range(4):
        r = tf.rotate(r, Direction.E)
    assert r == unit
    assert tf.mirror_x(tf.mirror_x(unit)) == unit
    assert tf.mirror_y(tf.mirror_y(unit)) == unit
    assert tf.translate(tf.translate(unit, 3, -7), -3, 7) == unit


@given(layouts())
def test_mirror_twice_identity(layout: Layout) -> None:
    assert tf.mirror_x(tf.mirror_x(layout)) == layout
    assert tf.mirror_y(tf.mirror_y(layout)) == layout


@given(layouts(), cardinals)
def test_transformed_tiles_equal_transformed_tile_set(
    gd: GameData, layout: Layout, d: Direction
) -> None:
    tiles = layout.tiles(gd)
    assert tf.rotate(layout, d).tiles(gd) == {t.rotate(d) for t in tiles}
    assert tf.mirror_x(layout).tiles(gd) == {t.mirror_x() for t in tiles}
    assert tf.mirror_y(layout).tiles(gd) == {t.mirror_y() for t in tiles}
    assert tf.translate(layout, 2, -5).tiles(gd) == {t.translate(2, -5) for t in tiles}


def test_interface_transforms() -> None:
    i = Interface("item", "iron-ore", Tile(0, 0), Direction.E, "left", 7.5, "input", ["x"])
    u = Unit(Layout([]), [i], {"k": 1})
    m = tf.mirror_y(u).interfaces[0]
    assert m.lane == "right" and m.direction == Direction.E and m.tile == Tile(0, -1)
    r = tf.rotate(u, Direction.E).interfaces[0]
    assert r.lane == "left" and r.direction == Direction.S and r.tile == Tile(-1, 0)
    mx = tf.mirror_x(u).interfaces[0]
    assert mx.lane == "right" and mx.direction == Direction.W


def test_bounds_and_normalize(gd: GameData) -> None:
    c = footprint.center_for_tile_anchor(gd, "stone-furnace", Tile(-3, 4))
    lay = Layout([Placement("stone-furnace", c), Placement("inserter", Vec2(5.5, 9.5))])
    w, h = footprint.size(gd, "stone-furnace")
    assert lay.bounds(gd) == (Tile(-3, 4), Tile(5, 9))
    n = tf.normalize(lay, gd)
    assert n.bounds(gd)[0] == Tile(0, 0)
    assert len(lay.tile_index(gd)) == w * h + 1


def test_render_small_layout(gd: GameData) -> None:
    f = footprint.center_for_tile_anchor(gd, "stone-furnace", Tile(0, 2))
    w, h = footprint.size(gd, "stone-furnace")
    placements = [Placement("transport-belt", Vec2(x + 0.5, 0.5), Direction.E) for x in range(w)]
    placements += [
        Placement("inserter", Vec2(0.5, 1.5), Direction.N),
        Placement("small-electric-pole", Vec2(1.5, 1.5)),
        Placement("stone-furnace", f),
        Placement("transport-belt", Vec2(0.5, 2.0 + h + 0.5), Direction.W),
    ]
    out = to_ascii(Layout(placements), gd)
    lines = out.splitlines()
    assert lines[0] == ">" * w
    assert lines[1].startswith("iP")
    assert lines[2 : 2 + h] == ["F" * w] * h
    assert lines[2 + h] == "<" + EMPTY * (w - 1)
    ruled = to_ascii(Layout(placements), gd, rulers=True).splitlines()
    assert ruled[0].strip().startswith("01")


def test_render_overlap_and_unknown(gd: GameData) -> None:
    lay = Layout(
        [
            Placement("wooden-chest", Vec2(0.5, 0.5)),
            Placement("iron-chest", Vec2(0.5, 0.5)),
            Placement("locomotive", Vec2(2.5, 0.5)),
        ]
    )
    assert to_ascii(lay, gd) == OVERLAP + EMPTY + "?"


def test_ports_on_two_by_two_poles_follow_the_entity_under_every_pose(gd):
    """A substation's centre sits on a tile corner: rotating its port tile as a tile
    lands one tile off the rotated entity (phase 8: the circuit chain's power port).
    With ``position`` the port follows the placement under every pose."""
    from dataclasses import replace

    from fgen.layout.model import Pose
    from fgen.layout.transform import place_unit
    from fgen.layout.validate import check_contract

    pole = "substation"
    pos = footprint.center_for_tile_anchor(gd, pole, Tile(3, 5))
    unit = Unit(
        Layout([Placement(pole, pos)]),
        [
            Interface(
                "power",
                None,
                pos.floor(),
                Direction.E,
                None,
                None,
                "input",
                entity=pole,
                position=pos,
            )
        ],
    )
    assert check_contract(unit, gd) == []
    for rotation in (Direction.N, Direction.E, Direction.S, Direction.W):
        for mirror in (False, True):
            w = place_unit(unit, Tile(10, -4), Pose(rotation, mirror))
            p, i = w.layout.placements[0], w.interfaces[0]
            assert i.position == p.position and i.tile == p.position.floor()
            assert i.tile in p.tiles(gd)
            assert check_contract(w, gd) == []
    # without the centre the contract check says so
    bare = Unit(unit.layout, [replace(unit.interfaces[0], position=None)])
    assert [v.code for v in check_contract(bare, gd)] == ["contract.entity"]
