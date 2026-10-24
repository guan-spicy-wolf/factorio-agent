"""Shared hypothesis strategies."""

from hypothesis import strategies as st

from fgen.data.loader import load_seed
from fgen.geom import footprint
from fgen.geom.vec import CARDINALS, Direction, Tile
from fgen.layout.model import Interface, Layout, Placement, Unit

GD = load_seed()
NAMES = sorted(GD.entities)

json_scalars = st.none() | st.booleans() | st.integers(-(2**31), 2**31) | st.text(max_size=8)
extras = st.dictionaries(
    st.sampled_from(["recipe", "items", "mirror", "type", "filters", "bar", "custom"]),
    st.recursive(
        json_scalars,
        lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=5), c, max_size=3),
        max_leaves=6,
    ),
    max_size=3,
)


@st.composite
def placements(draw: st.DrawFn, *, cardinal_only: bool = True) -> Placement:
    name = draw(st.sampled_from(NAMES))
    d = draw(st.sampled_from(CARDINALS if cardinal_only else list(Direction)))
    anchor = Tile(draw(st.integers(-60, 60)), draw(st.integers(-60, 60)))
    fd = d if d.is_cardinal else Direction.N
    pos = footprint.center_for_tile_anchor(GD, name, anchor, fd)
    return Placement(name, pos, d, draw(extras))


def layouts(*, cardinal_only: bool = True) -> st.SearchStrategy[Layout]:
    return st.lists(placements(cardinal_only=cardinal_only), max_size=12).map(Layout)


@st.composite
def units(draw: st.DrawFn) -> Unit:
    layout = draw(layouts())
    ifaces = draw(
        st.lists(
            st.builds(
                Interface,
                kind=st.sampled_from(["item", "fluid", "power"]),
                material=st.none() | st.sampled_from(["iron-ore", "coal"]),
                tile=st.builds(Tile, st.integers(-50, 50), st.integers(-50, 50)),
                direction=st.sampled_from(CARDINALS),
                lane=st.sampled_from(["left", "right", "both", None]),
                rate=st.none() | st.floats(0, 100),
                role=st.sampled_from(["input", "output"]),
                assumptions=st.just([]),
            ),
            max_size=4,
        )
    )
    return Unit(layout, ifaces, {})
