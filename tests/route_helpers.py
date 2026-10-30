"""Small hand-made units for routing tests."""

from __future__ import annotations

from fgen.data.schema import GameData
from fgen.geom import belt as beltgeom
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import (
    Interface,
    LaneMaterial,
    Layout,
    MinRate,
    Placement,
    PortGroup,
    PortSpace,
    RateProfile,
    Unit,
)
from fgen.terrain.model import Terrain, area_tiles

E = Direction.E


def belt_line(
    gd: GameData,
    n: int,
    *,
    role: str,
    material: str = "iron-ore",
    rate: float | None = 5.0,
    lane: str = "both",
    belt: str = "transport-belt",
    requires: tuple = (),
    extra_ifaces: list[Interface] | None = None,
) -> Unit:
    """``n`` east-flowing belts on row 0 starting at (0, 0). An output unit's port is the
    last tile, an input unit's the first (its last tile is a declared terminal).
    ``rate=None``: on-demand supply (no limit of its own)."""
    ps = [Placement(belt, Tile(x, 0).center(), E) for x in range(n)]
    tile = Tile(n - 1, 0) if role == "output" else Tile(0, 0)
    profile = RateProfile.on_demand() if rate is None else None
    approach = "both" if role == "input" and lane in ("left", "right") else "direct"
    i = Interface(
        "item",
        material,
        tile,
        E,
        lane,
        rate,
        role,
        requires=requires,
        rate_profile=profile,
        entity=belt,
        port_group="belt",
        space=PortSpace.item(approach),
        buffer_items=beltgeom.run_items(gd, n, lane),
    )
    meta = {"belt_terminals": [Tile(n - 1, 0)]} if role == "input" else {}
    return Unit(Layout(ps), [i, *(extra_ifaces or [])], meta, (PortGroup("belt"),))


def ore_and_coal_input(gd: GameData, n: int = 6, ore_rate: float = 5.0) -> Unit:
    """An input belt wanting ore on the left lane and coal on the right."""
    ore = Interface(
        "item",
        "iron-ore",
        Tile(0, 0),
        E,
        "left",
        ore_rate,
        "input",
        requires=(MinRate(ore_rate),),
        entity="transport-belt",
        port_group="feed",
        space=PortSpace.item("both"),
        buffer_items=beltgeom.run_items(gd, n, "left"),
    )
    coal = Interface(
        "item",
        "coal",
        Tile(0, 0),
        E,
        "right",
        0.5,
        "input",
        requires=(MinRate(0.5),),
        entity="transport-belt",
        port_group="feed",
        space=PortSpace.item("both"),
        buffer_items=beltgeom.run_items(gd, n, "right"),
    )
    ps = [Placement("transport-belt", Tile(x, 0).center(), E) for x in range(n)]
    group = PortGroup("feed", (LaneMaterial("left", "iron-ore"), LaneMaterial("right", "coal")))
    return Unit(Layout(ps), [ore, coal], {"belt_terminals": [Tile(n - 1, 0)]}, (group,))


def flat(x0: int, y0: int, x1: int, y1: int, **kw) -> Terrain:
    return Terrain.from_resources({}, area=(Tile(x0, y0), Tile(x1, y1)), **kw)


def rect(x0: int, y0: int, x1: int, y1: int) -> frozenset[Tile]:
    return area_tiles((Tile(x0, y0), Tile(x1, y1)))
