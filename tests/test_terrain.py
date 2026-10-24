"""M1 (phase 2): terrain snapshot, patch splitting and painted shapes (no server)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from fgen.geom.vec import Tile
from fgen.terrain import paint
from fgen.terrain.model import Patch, Terrain, area_tiles, components, nearest_patch, patches


def _flood_count(tiles: set[Tile]) -> int:
    """Independent reference: count 4-connected components by repeated BFS."""
    seen: set[Tile] = set()
    n = 0
    for t in sorted(tiles):
        if t in seen:
            continue
        n += 1
        queue = [t]
        seen.add(t)
        while queue:
            a = queue.pop(0)
            for b in (
                Tile(a.x + 1, a.y),
                Tile(a.x - 1, a.y),
                Tile(a.x, a.y + 1),
                Tile(a.x, a.y - 1),
            ):
                if b in tiles and b not in seen:
                    seen.add(b)
                    queue.append(b)
    return n


rects = st.tuples(st.integers(-30, 30), st.integers(-30, 30), st.integers(1, 8), st.integers(1, 8))


@settings(max_examples=60, deadline=None)
@given(st.lists(rects, min_size=1, max_size=6), st.integers(1, 1000))
def test_patches_partition_painted_rectangles(rs, amount):
    res: dict[Tile, tuple[str, int]] = {}
    for x, y, w, h in rs:
        res.update(paint.to_resources(paint.rectangle(x, y, w, h, amount), "iron-ore"))
    terrain = Terrain.from_resources(res)
    ps = patches(terrain, "iron-ore")
    union: set[Tile] = set()
    for p in ps:
        assert not (union & p.tiles)  # disjoint
        union |= p.tiles
        assert components(p.tiles) == [p.tiles]  # each patch is connected
        assert p.total_amount == amount * len(p.tiles)
    assert union == set(res)
    assert len(ps) == _flood_count(set(res))


@settings(max_examples=40, deadline=None)
@given(st.integers(0, 10_000), st.integers(1, 120))
def test_blob_is_one_patch(seed, n):
    shape = paint.blob(seed, n, 500, cx=3, cy=-2)
    assert len(shape) == n and Tile(3, -2) in shape
    ps = patches(Terrain.from_resources(paint.to_resources(shape, "copper-ore")), "copper-ore")
    assert len(ps) == 1 and ps[0].tiles == frozenset(shape)
    assert paint.blob(seed, n, 500, cx=3, cy=-2) == shape  # deterministic


def test_two_resources_split_separately():
    res = paint.to_resources(paint.rectangle(0, 0, 4, 4, 10), "iron-ore")
    res.update(paint.to_resources(paint.rectangle(4, 0, 4, 4, 10), "copper-ore"))
    t = Terrain.from_resources(res)
    assert [len(p.tiles) for p in patches(t, "iron-ore")] == [16]
    assert [len(p.tiles) for p in patches(t, "copper-ore")] == [16]
    assert patches(t, "coal") == []


def test_diagonal_tiles_are_separate_patches():
    res = paint.to_resources({Tile(0, 0): 5, Tile(1, 1): 5}, "iron-ore")
    assert len(patches(Terrain.from_resources(res), "iron-ore")) == 2


def test_patch_bounds_and_amount():
    shape = paint.l_shape(2, 3, 10, 8, 3, 100)
    (p,) = patches(Terrain.from_resources(paint.to_resources(shape, "iron-ore")), "iron-ore")
    assert p.bounds == (Tile(2, 3), Tile(11, 10))
    assert p.size == (10, 8)
    assert len(p.tiles) == 3 * 8 + 10 * 3 - 3 * 3
    assert p.total_amount == 100 * len(p.tiles)
    assert "45 tiles" in p.describe()


def test_hole_and_spec():
    spec = paint.ShapeSpec(
        "rectangle", {"x": 0, "y": 0, "w": 9, "h": 9, "amount": 7}, ((3, 3, 3, 3),)
    )
    shape = spec.build()
    assert len(shape) == 81 - 9 and Tile(4, 4) not in shape and Tile(2, 2) in shape
    assert "hole" in spec.describe()
    assert set(spec.resources().values()) == {("iron-ore", 7)}


def test_terrain_from_resources_buildable():
    res = paint.to_resources(paint.rectangle(0, 0, 2, 2, 1), "stone")
    water = {Tile(5, 5)}
    t = Terrain.from_resources(res, margin=3, water=water, occupied={Tile(-1, -1)})
    assert t.buildable == area_tiles((Tile(-3, -3), Tile(4, 4)))  # (5,5) is outside anyway
    t = Terrain.from_resources(res, area=(Tile(-3, -3), Tile(6, 6)), water=water)
    assert Tile(5, 5) not in t.buildable and t.water == frozenset(water)
    assert t.can_build(Tile(0, 0))  # ore tiles are buildable
    t2 = Terrain.from_resources(res, occupied={Tile(0, 0)})
    assert not t2.can_build(Tile(0, 0))
    assert t2.resource_at(Tile(1, 1)) == ("stone", 1) and t2.resource_at(Tile(9, 9)) is None


def test_nearest_patch():
    res = paint.to_resources(paint.rectangle(20, 0, 5, 5, 1), "iron-ore")
    res.update(paint.to_resources(paint.rectangle(-10, 0, 1, 1, 1), "iron-ore"))
    res.update(paint.to_resources(paint.rectangle(-40, 0, 6, 6, 1), "iron-ore"))
    t = Terrain.from_resources(res)
    assert nearest_patch(t, "iron-ore", (0, 0)).bounds[0] == Tile(-10, 0)
    big = nearest_patch(t, "iron-ore", (0, 0), min_tiles=10)
    assert isinstance(big, Patch) and big.bounds[0] == Tile(20, 0)
    assert nearest_patch(t, "coal", (0, 0)) is None
