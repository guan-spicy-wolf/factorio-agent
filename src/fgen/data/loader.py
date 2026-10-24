"""Load game data from the hand-written seed or from a real ``--dump-data`` file.

The seed file uses exactly the shape of ``data-raw-dump.json`` (``type -> name ->
prototype``), so both paths go through the same extractor. The extractor reads only
the fields listed in ``_ENTITY_FIELDS`` / ``_OTHER_FIELDS``; ``fields_used()`` exposes
that table so the fields can be checked one by one against a real dump.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from fgen.data.schema import (
    BoundingBox,
    EnergySource,
    EntityProto,
    FluidBox,
    GameData,
    ItemProto,
    PipeConnection,
    RecipeProto,
    Vector,
)

_COMMON = ["name", "type", "collision_box", "tile_width", "tile_height", "flags"]
_ENERGY = ["energy_source.type", "energy_source.fuel_categories", "energy_usage"]

# Fields read per prototype type. Dotted paths address nested tables.
_ENTITY_FIELDS: dict[str, list[str]] = {
    "transport-belt": [*_COMMON, "speed"],
    "underground-belt": [*_COMMON, "speed", "max_distance"],
    "splitter": [*_COMMON, "speed"],
    "inserter": [
        *_COMMON,
        "pickup_position",
        "insert_position",
        "rotation_speed",
        "extension_speed",
        "energy_per_movement",
        "bulk",
        "energy_source.type",
    ],
    "electric-pole": [*_COMMON, "supply_area_distance", "maximum_wire_distance"],
    "furnace": [*_COMMON, "crafting_speed", "crafting_categories", *_ENERGY],
    "assembling-machine": [*_COMMON, "crafting_speed", "crafting_categories", *_ENERGY],
    "mining-drill": [
        *_COMMON,
        "mining_speed",
        "resource_searching_radius",
        "vector_to_place_result",
        *_ENERGY,
        "output_fluid_box.pipe_connections",
    ],
    "pipe": [*_COMMON, "fluid_box.pipe_connections"],
    "pipe-to-ground": [
        *_COMMON,
        "fluid_box.pipe_connections",
        "fluid_box.pipe_connections[].max_underground_distance",
    ],
    "offshore-pump": [*_COMMON, "fluid_box.pipe_connections", "energy_source.type"],
    "storage-tank": [*_COMMON, "fluid_box.pipe_connections"],
    "container": [*_COMMON, "inventory_size"],
}

_OTHER_FIELDS: dict[str, list[str]] = {
    "item": ["name", "type", "stack_size", "fuel_value", "fuel_category"],
    "recipe": [
        "name",
        "category",
        "energy_required",
        "ingredients[].name",
        "ingredients[].amount",
        "results[].name",
        "results[].amount",
    ],
}

_DIRECTION_NAMES = {
    "north": 0,
    "northnortheast": 1,
    "northeast": 2,
    "eastnortheast": 3,
    "east": 4,
    "eastsoutheast": 5,
    "southeast": 6,
    "southsoutheast": 7,
    "south": 8,
    "southsouthwest": 9,
    "southwest": 10,
    "westsouthwest": 11,
    "west": 12,
    "westnorthwest": 13,
    "northwest": 14,
    "northnorthwest": 15,
}

SEED_FILE = "vanilla-2.0.json"


def fields_used() -> dict[str, list[str]]:
    """Fields of ``data.raw`` that fgen reads, per prototype type."""
    out = {t: list(fs) for t, fs in _ENTITY_FIELDS.items()}
    out.update({t: list(fs) for t, fs in _OTHER_FIELDS.items()})
    return out


def _vec(v: Any) -> Vector:
    if isinstance(v, Mapping):
        return (float(v["x"]), float(v["y"]))
    x, y = v
    return (float(x), float(y))


def _box(v: Any) -> BoundingBox:
    if isinstance(v, Mapping):
        return (_vec(v["left_top"]), _vec(v["right_bottom"]))
    a, b = v[0], v[1]
    return (_vec(a), _vec(b))


def _direction(v: Any) -> int:
    if isinstance(v, str):
        return _DIRECTION_NAMES[v.replace("_", "").replace("-", "").lower()]
    return int(v)


def _pipe_connections(fb: Mapping[str, Any] | None) -> FluidBox | None:
    if fb is None:
        return None
    conns = []
    for c in fb.get("pipe_connections", []):
        if "position" not in c:
            # Pre-2.0 style (``positions`` per direction) is not supported.
            raise ValueError(f"pipe connection without 'position' (pre-2.0 format?): {c!r}")
        mud = c.get("max_underground_distance")
        conns.append(
            PipeConnection(
                position=_vec(c["position"]),
                direction=_direction(c.get("direction", 0)),
                connection_type=c.get("connection_type", "normal"),
                flow_direction=c.get("flow_direction", "input-output"),
                max_underground_distance=int(mud) if mud is not None else None,
            )
        )
    return FluidBox(pipe_connections=tuple(conns))


def _energy_source(es: Mapping[str, Any] | None) -> EnergySource | None:
    if es is None:
        return None
    cats = es.get("fuel_categories")
    if cats is None and "fuel_category" in es:
        cats = [es["fuel_category"]]
    return EnergySource(type=es["type"], fuel_categories=tuple(cats or ()))


def _opt_float(raw: Mapping[str, Any], key: str) -> float | None:
    v = raw.get(key)
    return float(v) if v is not None else None


def _opt_int(raw: Mapping[str, Any], key: str) -> int | None:
    v = raw.get(key)
    return int(v) if v is not None else None


def _entity(raw: Mapping[str, Any]) -> EntityProto:
    fb = _pipe_connections(raw.get("fluid_box"))
    mud = None
    if fb is not None:
        for c in fb.pipe_connections:
            if c.max_underground_distance is not None:
                mud = c.max_underground_distance
    if raw.get("max_underground_distance") is not None:  # pre-2.0 location
        mud = int(raw["max_underground_distance"])
    return EntityProto(
        name=raw["name"],
        type=raw["type"],
        collision_box=_box(raw["collision_box"]),
        tile_width=_opt_int(raw, "tile_width"),
        tile_height=_opt_int(raw, "tile_height"),
        flags=tuple(raw.get("flags", ())),
        energy_source=_energy_source(raw.get("energy_source")),
        energy_usage=raw.get("energy_usage"),
        pickup_position=_vec(raw["pickup_position"]) if "pickup_position" in raw else None,
        insert_position=_vec(raw["insert_position"]) if "insert_position" in raw else None,
        rotation_speed=_opt_float(raw, "rotation_speed"),
        extension_speed=_opt_float(raw, "extension_speed"),
        energy_per_movement=raw.get("energy_per_movement"),
        bulk=bool(raw.get("bulk", False)),
        supply_area_distance=_opt_float(raw, "supply_area_distance"),
        maximum_wire_distance=_opt_float(raw, "maximum_wire_distance"),
        speed=_opt_float(raw, "speed"),
        max_distance=_opt_int(raw, "max_distance"),
        crafting_speed=_opt_float(raw, "crafting_speed"),
        crafting_categories=tuple(raw.get("crafting_categories", ())),
        mining_speed=_opt_float(raw, "mining_speed"),
        resource_searching_radius=_opt_float(raw, "resource_searching_radius"),
        vector_to_place_result=(
            _vec(raw["vector_to_place_result"]) if "vector_to_place_result" in raw else None
        ),
        fluid_box=fb,
        output_fluid_box=_pipe_connections(raw.get("output_fluid_box")),
        max_underground_distance=mud,
        inventory_size=_opt_int(raw, "inventory_size"),
    )


def _products(raw: Mapping[str, Any], key: str) -> tuple[tuple[str, float], ...]:
    out = []
    for p in raw.get(key) or ():
        if isinstance(p, Mapping):
            out.append((p["name"], float(p.get("amount", 1))))
        else:  # 1.1 short form ["iron-ore", 1]
            out.append((p[0], float(p[1])))
    return tuple(out)


def _recipe(raw: Mapping[str, Any]) -> RecipeProto:
    results = _products(raw, "results")
    if not results and "result" in raw:  # 1.1 short form
        results = ((raw["result"], float(raw.get("result_count", 1))),)
    return RecipeProto(
        name=raw["name"],
        category=raw.get("category", "crafting"),
        energy_required=float(raw.get("energy_required", 0.5)),
        ingredients=_products(raw, "ingredients"),
        results=results,
    )


def _item(raw: Mapping[str, Any]) -> ItemProto:
    return ItemProto(
        name=raw["name"],
        type=raw.get("type", "item"),
        stack_size=int(raw["stack_size"]),
        fuel_value=raw.get("fuel_value"),
        fuel_category=raw.get("fuel_category"),
    )


def from_raw(
    raw: Mapping[str, Any],
    *,
    version: str,
    source: Literal["seed", "dump"],
    constants: Mapping[str, float],
) -> GameData:
    """Build ``GameData`` from a ``data.raw``-shaped mapping."""
    entities: dict[str, EntityProto] = {}
    for proto_type in _ENTITY_FIELDS:
        for name, proto in (raw.get(proto_type) or {}).items():
            entities[name] = _entity({"name": name, "type": proto_type, **proto})
    recipes = {n: _recipe({"name": n, **r}) for n, r in (raw.get("recipe") or {}).items()}
    items = {n: _item({"name": n, **i}) for n, i in (raw.get("item") or {}).items()}
    return GameData(
        entities=entities,
        recipes=recipes,
        version=version,
        source=source,
        items=items,
        constants=dict(constants),
    )


def _seed_raw() -> dict[str, Any]:
    text = resources.files("fgen.data").joinpath("seed", SEED_FILE).read_text(encoding="utf-8")
    return json.loads(text)


def load_seed() -> GameData:
    """Load the hand-written vanilla 2.0 seed data (values from memory; see VERIFY.md)."""
    raw = _seed_raw()
    return from_raw(raw, version=raw["_version"], source="seed", constants=raw["_constants"])


def load_dump(
    path: str | Path,
    *,
    version: str | None = None,
    constants: Mapping[str, float] | None = None,
) -> GameData:
    """Load a real ``data-raw-dump.json`` from ``factorio --dump-data``.

    The dump carries neither the game version nor engine constants (ticks per second,
    items per belt tile); unless given explicitly they are taken from the seed file.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    seed = _seed_raw()
    return from_raw(
        raw,
        version=version or raw.get("_version") or seed["_version"],
        source="dump",
        constants=constants if constants is not None else seed["_constants"],
    )
