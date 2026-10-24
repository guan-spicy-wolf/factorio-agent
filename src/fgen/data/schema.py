"""Dataclasses mirroring the subset of Factorio ``data.raw`` that fgen reads.

Field names intentionally match ``data.raw`` (and ``factorio --dump-data``) so that a
real dump can be loaded without renaming. Only fields the geometry library, the
validator or the generators actually read are present; see ``loader.fields_used()``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

type Vector = tuple[float, float]
type BoundingBox = tuple[Vector, Vector]

_SI_PREFIX = {"": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15}
_ENERGY_RE = re.compile(r"^\s*([0-9.eE+-]+)\s*([kKMGTP]?)([WJ])\s*$")


def parse_energy(value: str | float | int) -> float:
    """Parse a Factorio energy string such as ``"90kW"`` or ``"4MJ"``.

    Returns watts for ``W`` and joules for ``J``. Plain numbers are returned as-is.
    """
    if isinstance(value, int | float):
        return float(value)
    m = _ENERGY_RE.match(value)
    if not m:
        raise ValueError(f"unparseable energy value: {value!r}")
    number, prefix, _unit = m.groups()
    return float(number) * _SI_PREFIX[prefix]


@dataclass(frozen=True)
class EnergySource:
    type: str  # "electric" | "burner" | "void" | "heat" | "fluid"
    fuel_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipeConnection:
    """2.0 pipe connection: ``position`` lies inside the entity (relative to its centre,
    for the north-facing entity) and ``direction`` points outward from that tile."""

    position: Vector
    direction: int
    connection_type: str = "normal"  # "normal" | "underground" | "linked"
    flow_direction: str = "input-output"
    max_underground_distance: int | None = None


@dataclass(frozen=True)
class FluidBox:
    pipe_connections: tuple[PipeConnection, ...] = ()


@dataclass(frozen=True)
class EntityProto:
    name: str
    type: str
    collision_box: BoundingBox
    tile_width: int | None = None
    tile_height: int | None = None
    flags: tuple[str, ...] = ()
    energy_source: EnergySource | None = None
    energy_usage: str | None = None
    # inserter
    pickup_position: Vector | None = None
    insert_position: Vector | None = None
    rotation_speed: float | None = None
    extension_speed: float | None = None
    energy_per_movement: str | None = None
    bulk: bool = False
    # electric-pole
    supply_area_distance: float | None = None
    maximum_wire_distance: float | None = None
    # transport-belt / underground-belt / splitter
    speed: float | None = None
    max_distance: int | None = None
    # furnace / assembling-machine
    crafting_speed: float | None = None
    crafting_categories: tuple[str, ...] = ()
    # mining-drill
    mining_speed: float | None = None
    resource_searching_radius: float | None = None
    vector_to_place_result: Vector | None = None
    # fluids
    fluid_box: FluidBox | None = None
    output_fluid_box: FluidBox | None = None
    max_underground_distance: int | None = None
    # container
    inventory_size: int | None = None

    @property
    def is_electric(self) -> bool:
        return self.energy_source is not None and self.energy_source.type == "electric"

    @property
    def is_burner(self) -> bool:
        return self.energy_source is not None and self.energy_source.type == "burner"


@dataclass(frozen=True)
class ItemProto:
    name: str
    type: str
    stack_size: int
    fuel_value: str | None = None
    fuel_category: str | None = None


@dataclass(frozen=True)
class RecipeProto:
    name: str
    category: str
    energy_required: float
    ingredients: tuple[tuple[str, float], ...]
    results: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class GameData:
    entities: dict[str, EntityProto]
    recipes: dict[str, RecipeProto]
    version: str
    source: Literal["seed", "dump"]
    items: dict[str, ItemProto] = field(default_factory=dict)
    # Engine constants that are not part of any prototype (ticks per second, items per
    # belt tile). They live in the seed file, never in code.
    constants: dict[str, float] = field(default_factory=dict)

    def entity(self, name: str) -> EntityProto:
        try:
            return self.entities[name]
        except KeyError:
            raise KeyError(f"unknown entity {name!r} in {self.source} data") from None

    def recipe(self, name: str) -> RecipeProto:
        try:
            return self.recipes[name]
        except KeyError:
            raise KeyError(f"unknown recipe {name!r} in {self.source} data") from None

    def constant(self, name: str) -> float:
        try:
            return self.constants[name]
        except KeyError:
            raise KeyError(f"engine constant {name!r} missing from game data") from None
