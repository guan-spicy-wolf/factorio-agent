import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fgen.data.schema import GameData
from fgen.geom import belt, fluid, footprint, inserter, pole
from fgen.geom.vec import CARDINALS, Direction, Tile, Vec2

coords = st.floats(min_value=-1000, max_value=1000, allow_nan=False).map(lambda v: round(v * 2) / 2)
vecs = st.builds(Vec2, coords, coords)
tiles = st.builds(Tile, st.integers(-500, 500), st.integers(-500, 500))
cardinals = st.sampled_from(CARDINALS)
directions = st.sampled_from(list(Direction))


# ---- vec -------------------------------------------------------------------


def test_rotation_reference() -> None:
    assert Direction.N.vector() == Vec2(0, -1)
    assert Vec2(0, -1).rotate(Direction.E) == Vec2(1, 0)
    assert Direction.E.vector() == Vec2(1, 0)
    assert Direction.S.vector() == Vec2(0, 1)
    assert Direction.W.vector() == Vec2(-1, 0)


def test_direction_values() -> None:
    assert [Direction.N, Direction.NE, Direction.E, Direction.SE] == [0, 2, 4, 6]
    assert [Direction.S, Direction.SW, Direction.W, Direction.NW] == [8, 10, 12, 14]
    assert len(Direction) == 16
    assert Direction.from_legacy_8way(2) == Direction.E


@given(vecs, cardinals)
def test_vec_rotate_four_times_identity(v: Vec2, d: Direction) -> None:
    r = v
    for _ in range(4):
        r = r.rotate(d)
    assert r == v


@given(vecs, cardinals, cardinals)
def test_vec_rotate_composes(v: Vec2, a: Direction, b: Direction) -> None:
    assert v.rotate(a).rotate(b) == v.rotate(a.rotate_cw(b.quarter_turns))


@given(vecs)
def test_vec_mirror_involution(v: Vec2) -> None:
    assert v.mirror_x().mirror_x() == v
    assert v.mirror_y().mirror_y() == v


@given(directions)
def test_direction_algebra(d: Direction) -> None:
    assert d.rotate_cw(4) == d
    assert d.opposite.opposite == d
    assert d.rotate_cw(2) == d.opposite
    assert d.mirror_x().mirror_x() == d
    assert d.mirror_y().mirror_y() == d
    assert Direction.from_blueprint(d.to_blueprint()) == d


def test_direction_mirrors() -> None:
    assert Direction.E.mirror_x() == Direction.W
    assert Direction.N.mirror_x() == Direction.N
    assert Direction.N.mirror_y() == Direction.S
    assert Direction.E.mirror_y() == Direction.E
    assert Direction.NE.mirror_x() == Direction.NW
    assert Direction.NE.mirror_y() == Direction.SE


@given(cardinals)
def test_direction_vector_mirror_consistent(d: Direction) -> None:
    assert d.vector().mirror_x() == d.mirror_x().vector()
    assert d.vector().mirror_y() == d.mirror_y().vector()


def test_vec_rotate_rejects_diagonal() -> None:
    with pytest.raises(ValueError):
        Vec2(1, 0).rotate(Direction.NE)


@given(tiles, cardinals)
def test_tile_transforms_match_point_transforms(t: Tile, d: Direction) -> None:
    assert t.rotate(d).center() == t.center().rotate(d)
    assert t.mirror_x().center() == t.center().mirror_x()
    assert t.mirror_y().center() == t.center().mirror_y()


# ---- footprint ---------------------------------------------------------------


def _expected_size(gd: GameData, name: str) -> tuple[int, int]:
    (x1, y1), (x2, y2) = gd.entity(name).collision_box
    return math.ceil(x2 - x1), math.ceil(y2 - y1)


def test_footprint_sizes_follow_collision_box(gd: GameData) -> None:
    for name in gd.entities:
        w, h = _expected_size(gd, name)
        assert footprint.size(gd, name, Direction.N) == (w, h)
        assert footprint.size(gd, name, Direction.E) == (h, w)
        anchor = Tile(3, -2)
        for d in CARDINALS:
            c = footprint.center_for_tile_anchor(gd, name, anchor, d)
            ts = footprint.tiles_of(gd, name, c, d)
            dw, dh = footprint.size(gd, name, d)
            assert len(ts) == dw * dh
            assert min(ts) == anchor
            assert ts == {Tile(anchor.x + i, anchor.y + j) for i in range(dw) for j in range(dh)}


def test_footprint_parity(gd: GameData) -> None:
    # even sizes -> integer centre, odd sizes -> .5 centre
    for name in gd.entities:
        w, h = footprint.size(gd, name)
        c = footprint.center_for_tile_anchor(gd, name, Tile(0, 0))
        assert (c.x % 1 == 0) == (w % 2 == 0)
        assert (c.y % 1 == 0) == (h % 2 == 0)
        with pytest.raises(ValueError):
            footprint.tiles_of(gd, name, c + Vec2(0.5, 0), Direction.N)


def test_aabb_contains_centre_and_rotates(gd: GameData) -> None:
    lt, rb = footprint.aabb_of(gd, "splitter", Vec2(1, 0.5), Direction.N)
    lt2, rb2 = footprint.aabb_of(gd, "splitter", Vec2(0.5, 1), Direction.E)
    assert rb.x - lt.x == pytest.approx(rb2.y - lt2.y)
    assert rb.y - lt.y == pytest.approx(rb2.x - lt2.x)


@given(tiles, cardinals, cardinals, st.data())
def test_footprint_commutes_with_rotation(
    gd: GameData, anchor: Tile, d: Direction, rot: Direction, data: st.DataObject
) -> None:
    name = data.draw(st.sampled_from(sorted(gd.entities)))
    c = footprint.center_for_tile_anchor(gd, name, anchor, d)
    before = footprint.tiles_of(gd, name, c, d)
    after = footprint.tiles_of(gd, name, c.rotate(rot), d.rotate_cw(rot.quarter_turns))
    assert after == {t.rotate(rot) for t in before}
    mx = footprint.tiles_of(gd, name, c.mirror_x(), d.mirror_x())
    assert mx == {t.mirror_x() for t in before}
    my = footprint.tiles_of(gd, name, c.mirror_y(), d.mirror_y())
    assert my == {t.mirror_y() for t in before}


# ---- inserter ----------------------------------------------------------------

INSERTERS = [
    "burner-inserter",
    "inserter",
    "fast-inserter",
    "long-handed-inserter",
    "bulk-inserter",
]


@pytest.mark.parametrize("name", INSERTERS)
def test_inserter_n_picks_north_drops_south(gd: GameData, name: str) -> None:
    e = gd.entity(name)
    own = Tile(5, 7)
    c = own.center()
    pu = inserter.pickup_tile(gd, name, c, Direction.N)
    dr = inserter.drop_tile(gd, name, c, Direction.N)
    # expectations derived from the seed vectors, not hard-coded
    assert pu == Tile(own.x, own.y + math.floor(0.5 + e.pickup_position[1]))
    assert dr == Tile(own.x, own.y + math.floor(0.5 + e.insert_position[1]))
    assert pu.y < own.y < dr.y


@pytest.mark.parametrize("name", INSERTERS)
@pytest.mark.parametrize("d", CARDINALS)
def test_inserter_rotates_with_direction(gd: GameData, name: str, d: Direction) -> None:
    own = Tile(0, 0)
    pu_n, dr_n = inserter.reach_offsets(gd, name)
    pu = inserter.pickup_tile(gd, name, own.center(), d)
    dr = inserter.drop_tile(gd, name, own.center(), d)
    # N-facing picks up to the north; rotating the inserter rotates both reach vectors
    assert pu == own.neighbor(d, -pu_n.y)
    assert dr == own.neighbor(d.opposite, dr_n.y)


# ---- pole --------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["small-electric-pole", "medium-electric-pole", "big-electric-pole", "substation"]
)
def test_pole_supply_square(gd: GameData, name: str) -> None:
    e = gd.entity(name)
    c = footprint.center_for_tile_anchor(gd, name, Tile(10, 10))
    st_ = pole.supply_tiles(gd, name, c)
    side = round(2 * e.supply_area_distance)
    assert len(st_) == side * side
    xs = {t.x for t in st_}
    ys = {t.y for t in st_}
    assert (min(xs) + max(xs) + 1) / 2 == c.x
    assert (min(ys) + max(ys) + 1) / 2 == c.y
    # the pole's own tiles are inside its supply area
    assert footprint.tiles_of(gd, name, c, Direction.N) <= st_


def test_wire_reach_uses_smaller_distance(gd: GameData) -> None:
    small = gd.entity("small-electric-pole").maximum_wire_distance
    big = gd.entity("big-electric-pole").maximum_wire_distance
    assert small < big
    a = Vec2(0.5, 0.5)
    assert pole.wire_reaches(
        gd, "small-electric-pole", a, "small-electric-pole", a + Vec2(small, 0)
    )
    far = a + Vec2(small + 0.5, 0)
    assert not pole.wire_reaches(gd, "small-electric-pole", a, "big-electric-pole", far)
    assert pole.wire_reaches(gd, "big-electric-pole", Vec2(0, 0), "big-electric-pole", Vec2(big, 0))


# ---- belt --------------------------------------------------------------------

BELTS = ["transport-belt", "fast-transport-belt", "express-transport-belt"]


@pytest.mark.parametrize("name", BELTS)
def test_belt_capacity_from_seed(gd: GameData, name: str) -> None:
    e = gd.entity(name)
    expected = e.speed * gd.constants["ticks_per_second"] * gd.constants["belt_items_per_tile"]
    assert belt.items_per_second(gd, name) == pytest.approx(expected)
    assert belt.lane_items_per_second(gd, name) * gd.constants["belt_lanes"] == pytest.approx(
        expected
    )


def test_belt_tiers_ordered(gd: GameData) -> None:
    caps = [belt.items_per_second(gd, n) for n in BELTS]
    assert caps == sorted(caps) and len(set(caps)) == 3


def test_underground_range_and_next_tile(gd: GameData) -> None:
    for n in ["underground-belt", "fast-underground-belt", "express-underground-belt"]:
        assert belt.underground_partner_range(gd, n) == gd.entity(n).max_distance
    with pytest.raises(ValueError):
        belt.underground_partner_range(gd, "transport-belt")
    assert belt.next_tile(Tile(0, 0), Direction.E) == Tile(1, 0)
    assert belt.next_tile(Tile(0, 0), Direction.N) == Tile(0, -1)


def test_lanes() -> None:
    # flowing east: left lane is the north side
    assert belt.lane_side(Direction.E, "left") == Direction.N
    assert belt.lane_side(Direction.E, "right") == Direction.S
    # inserter north of an east belt moves south onto it: far lane = south = right
    assert belt.drop_lane(Direction.E, Direction.S) == "right"
    assert belt.drop_lane(Direction.E, Direction.N) == "left"
    with pytest.raises(ValueError):
        belt.drop_lane(Direction.E, Direction.E)


# ---- fluid -------------------------------------------------------------------


def test_pipe_connects_all_sides(gd: GameData) -> None:
    pts = fluid.connection_points(gd, "pipe", Vec2(0.5, 0.5), Direction.N)
    assert {t for t, _ in pts} == {Tile(0, 0)}
    assert {d for _, d in pts} == set(CARDINALS)


def test_pipe_to_ground_underground_filtered(gd: GameData) -> None:
    c = Vec2(0.5, 0.5)
    assert len(fluid.connection_points(gd, "pipe-to-ground", c, Direction.N)) == 1
    allp = fluid.connection_points(gd, "pipe-to-ground", c, Direction.E, include_underground=True)
    assert {d for _, d in allp} == {Direction.E, Direction.W}


@given(cardinals)
def test_fluid_points_rotate_with_entity(gd: GameData, d: Direction) -> None:
    for name in ["pumpjack", "storage-tank", "offshore-pump"]:
        c = footprint.center_for_tile_anchor(gd, name, Tile(0, 0), Direction.N)
        base = fluid.connection_points(gd, name, c, Direction.N)
        rotated = fluid.connection_points(gd, name, c.rotate(d), d)
        assert sorted(rotated) == sorted(
            (t.rotate(d), dd.rotate_cw(d.quarter_turns)) for t, dd in base
        )
        tiles = footprint.tiles_of(gd, name, c, Direction.N)
        for t, dd in base:
            assert t in tiles  # connection position lies inside the entity
            assert t.neighbor(dd) not in tiles  # and points outward
