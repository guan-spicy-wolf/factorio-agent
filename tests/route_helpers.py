"""Small hand-made units for routing tests."""

from __future__ import annotations

from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import Interface, LaneMaterial, Layout, MinRate, Placement, Unit
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
    last tile, an input unit's the first (its last tile is a declared terminal)."""
    ps = [Placement(belt, Tile(x, 0).center(), E) for x in range(n)]
    tile = Tile(n - 1, 0) if role == "output" else Tile(0, 0)
    i = Interface("item", material, tile, E, lane, rate, role, requires=requires)
    meta = {"belt_terminals": [Tile(n - 1, 0)]} if role == "input" else {}
    return Unit(Layout(ps), [i, *(extra_ifaces or [])], meta)


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
        requires=(LaneMaterial("left", "iron-ore"), MinRate(ore_rate)),
    )
    coal = Interface(
        "item",
        "coal",
        Tile(0, 0),
        E,
        "right",
        0.5,
        "input",
        requires=(LaneMaterial("right", "coal"), MinRate(0.5)),
    )
    ps = [Placement("transport-belt", Tile(x, 0).center(), E) for x in range(n)]
    return Unit(Layout(ps), [ore, coal], {"belt_terminals": [Tile(n - 1, 0)]})


def flat(x0: int, y0: int, x1: int, y1: int, **kw) -> Terrain:
    return Terrain.from_resources({}, area=(Tile(x0, y0), Tile(x1, y1)), **kw)


def rect(x0: int, y0: int, x1: int, y1: int) -> frozenset[Tile]:
    return area_tiles((Tile(x0, y0), Tile(x1, y1)))
