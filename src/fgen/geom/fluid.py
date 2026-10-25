"""Fluid connection points (2.0 ``pipe_connections``) and crafting-machine fluid ports.

Checked on 2.1.23 (VERIFY §13, plan-phase5a):

- a connection's ``position`` lies inside the entity (for the north-facing entity,
  relative to its centre) and its ``direction`` points to the neighbour it connects to;
- an entity placed with ``mirror`` is mirrored **first** (x -> -x, E <-> W) and **then**
  rotated with its direction;
- a crafting machine (``fluid_boxes``, each with a ``production_type``) assigns the
  recipe's fluid ingredients to its input boxes and fluid products to its output boxes:
  one fluid of a kind -> every box of that kind carries it (the chemical plant's two
  input corners both take petroleum gas for plastic); as many fluids as boxes -> in
  order; no fluid of a kind -> those boxes are idle (they do not connect in 2.1.23, but
  a recipe can be changed in game, so the validator and the solver treat an idle port
  facing a fluid entity as a conflict). Other counts were not observed and raise.
  ``fluid_boxes_off_when_no_fluid_recipe`` machines without a fluid recipe have no ports.
"""

from __future__ import annotations

from dataclasses import dataclass

from fgen.data.schema import EntityProto, GameData, PipeConnection
from fgen.geom.vec import Direction, Tile, Vec2


def _transform(c: PipeConnection, direction: Direction, mirror: bool) -> tuple[Vec2, Direction]:
    v, d = Vec2.of(c.position), Direction(c.direction)
    if mirror:
        v, d = v.mirror_x(), d.mirror_x()
    return v.rotate(direction), d.rotate_cw(Direction(direction).quarter_turns)


def _boxes(e: EntityProto) -> list[tuple[str | None, PipeConnection]]:
    out: list[tuple[str | None, PipeConnection]] = []
    for fb in (e.fluid_box, e.output_fluid_box):
        if fb is not None:
            out += [(None, c) for c in fb.pipe_connections]
    return out


def connection_points(
    gd: GameData,
    name: str,
    pos: Vec2,
    direction: Direction,
    *,
    include_underground: bool = False,
    mirror: bool = False,
) -> list[tuple[Tile, Direction]]:
    """Tiles of the entity that carry a pipe connection and the outward direction
    (every fluid box, idle or not)."""
    e = gd.entity(name)
    conns = [c for _, c in _boxes(e)] + [c for fb in e.fluid_boxes for c in fb.pipe_connections]
    out: list[tuple[Tile, Direction]] = []
    for c in conns:
        if c.connection_type != "normal" and not include_underground:
            continue
        v, d = _transform(c, direction, mirror)
        out.append(((pos + v).floor(), d))
    return out


@dataclass(frozen=True)
class Port:
    """One normal connection of a crafting machine: ``tile`` inside the machine,
    ``direction`` outward, ``fluid`` the recipe assigns to its box (None: idle),
    ``role`` the box's production type, ``box`` its index in ``fluid_boxes``."""

    tile: Tile
    direction: Direction
    fluid: str | None
    role: str
    box: int

    @property
    def outside(self) -> Tile:
        """The neighbour tile the connection faces."""
        return self.tile.neighbor(self.direction)


def recipe_fluids(gd: GameData, recipe: str) -> tuple[list[str], list[str]]:
    """Fluid ingredients and fluid products of ``recipe``, in recipe order."""
    r = gd.recipe(recipe)
    return (
        [n for n, _ in r.ingredients if gd.is_fluid(n)],
        [n for n, _ in r.results if gd.is_fluid(n)],
    )


def box_fluids(gd: GameData, machine: str, recipe: str | None) -> list[str | None]:
    """Per box of ``fluid_boxes``: the fluid ``recipe`` puts in it (None: idle)."""
    e = gd.entity(machine)
    out: list[str | None] = [None] * len(e.fluid_boxes)
    if recipe is None:
        return out
    for kind, fluids in zip(("input", "output"), recipe_fluids(gd, recipe), strict=True):
        idx = [i for i, fb in enumerate(e.fluid_boxes) if fb.production_type == kind]
        if len(fluids) > len(idx):
            raise ValueError(f"{machine} has {len(idx)} {kind} boxes, {recipe} needs {len(fluids)}")
        if len(fluids) == 1:
            for i in idx:
                out[i] = fluids[0]
        elif len(fluids) == len(idx):
            for i, f in zip(idx, fluids, strict=True):
                out[i] = f
        elif fluids:
            raise ValueError(
                f"{recipe} on {machine}: {len(fluids)} {kind} fluids for {len(idx)} boxes "
                "(box assignment not verified, VERIFY §13)"
            )
    return out


def machine_ports(
    gd: GameData,
    name: str,
    pos: Vec2,
    direction: Direction,
    recipe: str | None,
    mirror: bool = False,
) -> list[Port]:
    """The normal connections of a crafting machine's ``fluid_boxes`` with the fluid
    ``recipe`` assigns to each (empty for machines without fluid boxes, and for
    ``fluid_boxes_off_when_no_fluid_recipe`` machines on a recipe without fluids)."""
    e = gd.entity(name)
    if not e.fluid_boxes:
        return []
    fluids = box_fluids(gd, name, recipe)
    if e.fluid_boxes_off_when_no_fluid_recipe and not any(fluids):
        return []
    out = []
    for k, (fb, f) in enumerate(zip(e.fluid_boxes, fluids, strict=True)):
        for c in fb.pipe_connections:
            if c.connection_type != "normal":
                continue
            v, d = _transform(c, direction, mirror)
            out.append(Port((pos + v).floor(), d, f, fb.production_type or "input-output", k))
    return out


def mirrored(extra: dict[str, object]) -> bool:
    """The blueprint ``mirror`` flag of a placement."""
    return bool(extra.get("mirror", False))


IDLE = "<idle>"  # label of an idle machine port (its box carries nothing)


def entity_ports(
    gd: GameData,
    name: str,
    pos: Vec2,
    direction: Direction,
    extra: dict[str, object],
    fluid: str | None = None,
) -> list[tuple[Tile, Direction, str | None]]:
    """Normal connections of any fluid entity as ``(tile, outward direction, label)``:
    a crafting machine's ports carry their recipe fluid (:data:`IDLE` when idle), any
    other entity's carry ``fluid`` (None: unknown)."""
    if gd.entity(name).fluid_boxes:
        recipe = extra.get("recipe")
        return [
            (pt.tile, pt.direction, pt.fluid if pt.fluid is not None else IDLE)
            for pt in machine_ports(
                gd, name, pos, direction, str(recipe) if recipe else None, mirrored(extra)
            )
        ]
    return [(t, d, fluid) for t, d in connection_points(gd, name, pos, direction)]


def has_fluid_ports(gd: GameData, name: str) -> bool:
    e = gd.entity(name)
    return e.fluid_box is not None or e.output_fluid_box is not None or bool(e.fluid_boxes)
